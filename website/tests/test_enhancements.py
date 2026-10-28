"""2.1增强检查：独立临时SQLite库；不修改课堂工作库或原40项检查。"""
import csv
import hashlib
import html
import io
import json
import platform
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from flask import template_rendered
from werkzeug.datastructures import MultiDict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app
from database import connect
from models import ZHENGZHOU_TIMEZONE, zhengzhou_today


def hidden(response, name):
    match = re.search(r'name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', response.get_data(as_text=True))
    if not match:
        raise AssertionError('Missing hidden field: ' + name)
    return html.unescape(match.group(1))


class EnhancementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='Meiti_enhancements_')
        cls.database = Path(cls.temporary.name) / 'enhancements.sqlite'
        cls.app = create_app({'TESTING': True, 'DATABASE': str(cls.database),
                              'SECRET_KEY': 'enhancement-tests-only'})
        cls.baseline = sqlite3.connect(':memory:')
        with closing(connect(cls.database)) as db:
            db.backup(cls.baseline)
            cls.schema = list(db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))

    @classmethod
    def tearDownClass(cls):
        cls.baseline.close()
        cls.temporary.cleanup()

    def setUp(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.baseline.backup(db)
        self.client = self.app.test_client()
        today = zhengzhou_today()
        self.week = (today - timedelta(days=today.weekday())).isoformat()
        self.fixed_now = datetime(2030, 1, 7, 12, 0, tzinfo=ZHENGZHOU_TIMEZONE)

    def query(self, sql, parameters=()):
        with closing(connect(self.database)) as db:
            return [dict(row) for row in db.execute(sql, parameters)]

    def scalar(self, sql, parameters=()):
        with closing(connect(self.database)) as db:
            return db.execute(sql, parameters).fetchone()[0]

    def write(self, sql, parameters=()):
        with closing(connect(self.database)) as db, db:
            return db.execute(sql, parameters).lastrowid

    def snapshot(self):
        with closing(connect(self.database)) as db:
            return '\n'.join(db.iterdump())

    def login(self, username='hr', client=None):
        client = client or self.client
        response = client.post('/login', data={'csrf_token': hidden(client.get('/login'), 'csrf_token'),
                                               'username': username, 'password': 'Demo!2026'})
        self.assertEqual(response.status_code, 302)
        return client

    def csrf(self):
        with self.client.session_transaction() as session:
            return session['csrf']

    def get_context(self, path, client=None, expected=200, method='get', data=None):
        captured = []

        def record(sender, template, context, **extra):
            captured.append((template.name, dict(context)))

        with template_rendered.connected_to(record, self.app):
            response = getattr(client or self.client, method)(path, data=data)
        self.assertEqual(response.status_code, expected, response.get_data(as_text=True)[:250])
        self.assertTrue(captured, path + ' did not render a template')
        return response, captured[-1][1]

    def add_employee(self, name, department='', position='', **extra):
        columns = ['name', 'department', 'position', *extra]
        return self.write('INSERT INTO employees (' + ','.join(columns) + ') VALUES (' +
                          ','.join('?' for unused in columns) + ')', [name, department, position, *extra.values()])

    def save_fixture_week(self, week, members=(1, 3, 4), shifts=('Morning', 'Afternoon')):
        with closing(connect(self.database)) as db, db:
            db.execute('DELETE FROM schedule_weeks WHERE team_id=1 AND week=?', (week,))
            schedule = db.execute('INSERT INTO schedule_weeks(team_id,week) VALUES(1,?)', (week,)).lastrowid
            start = date.fromisoformat(week)
            db.executemany('INSERT INTO assignments VALUES(?,?,?,?)',
                           [(schedule, member, (start + timedelta(days=day)).isoformat(), shift)
                            for member in members for day in range(5) for shift in shifts])
        return schedule

    def schedule_form(self, week, version=0, members=(1, 3, 4), **extra):
        data = MultiDict({'csrf_token': self.csrf(), 'week': week, 'version': str(version), **extra})
        for member in members:
            for day in range(5):
                value = (date.fromisoformat(week) + timedelta(days=day)).isoformat()
                data.setlist(f'shift_{member}_{value}', ['Morning', 'Afternoon'])
        return data

    def csv_rows(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'\xef\xbb\xbf'))
        return list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))

    def insert_checkins(self, count=25):
        with closing(connect(self.database)) as db, db:
            db.executemany('INSERT INTO checkins(employee_id,operation_id,recorded_at) VALUES(?,?,?)',
                           [(1, f'owned-{i}', f'2030-01-{i:02}T' +
                             ('00:00:00' if i == 1 else '23:59:59' if i == count else '12:00:00') + '+08:00')
                            for i in range(1, count + 1)])
            db.execute("INSERT INTO checkins(employee_id,operation_id,recorded_at) VALUES(2,'other-person','2030-01-10T12:00:00+08:00')")

    def test_EH01_hr_dashboard_uses_actual_counts(self):
        self.add_employee('Dashboard Fixture', 'New Dept', 'New Role')
        self.login()
        _, context = self.get_context('/dashboard')
        self.assertEqual(context['total'], 501)
        for field, value in [('department', 'New Dept'), ('position', 'New Role')]:
            counts = {row['label']: row['total'] for row in context['grouped'][field]}
            self.assertEqual(counts[value], 1)
            self.assertEqual(sum(counts.values()), 501)

    def test_EH02_supervisor_dashboard_is_scoped(self):
        self.login('supervisor')
        response, context = self.get_context('/dashboard')
        self.assertEqual(context['team'].id, 1)
        self.assertEqual({row['id'] for row in context['members']}, {1, 3, 4})
        self.assertTrue(context['saved'])
        self.assertNotIn('Chen Rui', response.get_data(as_text=True))
        for secret in ['Fictional address', 'Hourly wage: 25 CNY', 'Fictional contact']:
            self.assertNotIn(secret, response.get_data(as_text=True))

    def test_EH03_employee_dashboard_uses_bound_identity(self):
        self.login('employee')
        response, context = self.get_context('/dashboard')
        self.assertEqual(context['employee'].id, 1)
        self.assertNotIn('Chen Rui', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/dashboard?employee_id=2').status_code, 403)

    def test_EH04_home_and_dashboard_login_requirements(self):
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        for account in ['hr', 'supervisor', 'employee']:
            client = self.app.test_client()
            self.login(account, client)
            self.assertEqual(urlsplit(client.get('/').location).path, '/dashboard')
            self.assertEqual(client.get('/dashboard').status_code, 200)

    def test_EH05_directory_combines_filters_and_real_options(self):
        wanted = self.add_employee('Literal %_ Match', 'Specific Dept', 'Specific Role')
        self.add_employee('Literal ZZ Match', 'Specific Dept', 'Specific Role')
        self.add_employee('Literal %_ Match', 'Other Dept', 'Specific Role')
        self.login()
        args = urlencode({'name': '%_', 'department': 'Specific Dept', 'position': 'Specific Role'})
        _, context = self.get_context('/employees?' + args)
        self.assertEqual(context['count'], 1)
        self.assertEqual([row['id'] for row in context['rows']], [wanted])
        self.assertIn('Specific Dept', str(context['department_options']))
        self.assertIn('Specific Role', str(context['position_options']))

    def test_EH06_empty_flags_do_not_consume_real_values(self):
        empty = self.add_employee('Empty Fixture')
        literal = self.add_employee('Empty Fixture', '__empty__', 'Unspecified')
        self.add_employee('Other Empty Fixture', 'Unspecified', '__empty__')
        self.login()
        _, context = self.get_context('/employees?name=Empty+Fixture&department_empty=1&position_empty=1')
        self.assertEqual([row['id'] for row in context['rows']], [empty])
        _, context = self.get_context('/employees?name=Empty+Fixture&department=__empty__&position=Unspecified')
        self.assertEqual([row['id'] for row in context['rows']], [literal])
        self.assertIn('__empty__', context['department_options'])
        self.assertIn('Unspecified', context['department_options'])
        departments = {row['value']: row['total'] for row in context['grouped']['department']}
        self.assertEqual((departments[''], departments['Unspecified'], departments['__empty__']), (1, 1, 1))
        self.assertEqual(self.client.get('/employees?department=__empty__&department_empty=1').status_code, 400)
        self.assertEqual(self.client.get('/employees?position=Unspecified&position_empty=1').status_code, 400)

    def test_EH07_directory_sort_whitelist_and_stable_ties(self):
        ids = [self.add_employee('Sort Fixture ' + suffix, department, position)
               for suffix, department, position in [('B', 'Z', 'A'), ('A', 'A', 'Z'), ('A', 'A', 'Z')]]
        self.login()
        expected = {'id_asc': ids, 'name_asc': [ids[1], ids[2], ids[0]],
                    'name_desc': [ids[0], ids[1], ids[2]], 'department_asc': [ids[1], ids[2], ids[0]],
                    'position_asc': ids}
        for sort, order in expected.items():
            _, context = self.get_context('/employees?' + urlencode({'name': 'Sort Fixture', 'sort': sort}))
            self.assertEqual([row['id'] for row in context['rows']], order, sort)
        self.assertEqual(self.client.get('/employees?sort=name%3BDROP+TABLE+employees').status_code, 400)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 503)

    def test_EH08_same_name_pagination_is_stable(self):
        expected = [self.add_employee('Page Tie Fixture') for unused in range(25)]
        self.login()
        actual = []
        for page in [1, 2]:
            _, context = self.get_context(f'/employees?name=Page+Tie+Fixture&sort=name_desc&page={page}')
            self.assertEqual(context['count'], 25)
            actual.extend(row['id'] for row in context['rows'])
        self.assertEqual(actual, expected)

    def test_EH09_directory_context_survives_record_actions(self):
        self.login()
        values = {'name': 'Demo Employee', 'department': 'Retail', 'position': 'Store Assistant',
                  'sort': 'name_desc', 'page': '2'}
        query = urlencode(values)
        response, context = self.get_context('/employees?' + query)
        identifier = context['rows'][0]['id']
        for path in [f'/employees/{identifier}', f'/employees/{identifier}/edit', f'/employees/{identifier}/delete']:
            page, unused = self.get_context(path + '?' + query)
            links = [html.unescape(value) for value in re.findall(r'href="([^"]+)"', page.get_data(as_text=True))]
            # 编辑/删除先返回同一详情，再由详情返回目录；每一步都应保留上下文。
            directory_links = [link for link in links if urlsplit(link).path in ['/employees', f'/employees/{identifier}']]
            self.assertTrue(any(all(parse_qs(urlsplit(link).query).get(key) == [value]
                                    for key, value in values.items()) for link in directory_links), path)
        page = self.client.post(f'/employees/{identifier}/edit?' + query,
                                data={'csrf_token': self.csrf(), 'version': '1', 'name': 'Edited Context Fixture'})
        self.assertEqual(page.status_code, 302)
        self.assertTrue(all(parse_qs(urlsplit(page.location).query).get(key) == [value]
                            for key, value in values.items()))
        deleted = self.client.post(f'/employees/{identifier}/delete?' + query,
                                   data={'csrf_token': self.csrf(), 'version': '2',
                                         'confirmation': 'Edited Context Fixture'})
        self.assertEqual(deleted.status_code, 302)
        self.assertEqual(urlsplit(deleted.location).path, '/employees')
        self.assertTrue(all(parse_qs(urlsplit(deleted.location).query).get(key) == [value]
                            for key, value in values.items()))

    def test_EH10_employee_csv_exports_all_matches_and_public_fields(self):
        ids = [self.add_employee(f'CSV Fixture {number:02}', 'CSV Dept', 'CSV Role') for number in range(27)]
        self.login()
        response = self.client.get('/employees/export.csv?name=CSV+Fixture&department=CSV+Dept&sort=name_desc&page=2')
        rows = self.csv_rows(response)
        self.assertEqual(response.headers['X-Total-Records'], '27')
        self.assertEqual(len(rows), 27)
        self.assertEqual(list(rows[0]), ['Record ID', 'Name', 'Age', 'Position', 'Department', 'Address',
                                        'Pay information', 'Emergency contact'])
        self.assertEqual([int(row['Record ID']) for row in rows], list(reversed(ids)))
        self.assertNotRegex(response.data.decode('utf-8-sig'), r'password_hash|csrf_token|operation_id|expires_at')
        empty = self.client.get('/employees/export.csv?name=NoSuchExportFixture')
        self.assertEqual(self.csv_rows(empty), [])
        self.assertEqual(empty.headers['X-Total-Records'], '0')

    def test_EH11_employee_csv_neutralizes_formula_and_control_prefixes(self):
        dangerous = ['=1+1', '+SUM(A1)', '-2+3', '@lookup', '  =cmd', '\t@cmd', '\r=cmd', '\n+cmd', '\x01text', '\ufeff=cmd']
        expected = {}
        for value in dangerous:
            identifier = self.add_employee(value, 'Formula Dept', address=value, pay_info=value, emergency_contact=value)
            expected[identifier] = "'" + value
        benign = 'Ordinary, "quoted" text\nsecond line'
        expected[self.add_employee(benign, 'Formula Dept', address=benign, pay_info=benign, emergency_contact=benign)] = benign
        self.login()
        rows = self.csv_rows(self.client.get('/employees/export.csv?department=Formula+Dept'))
        self.assertEqual(len(rows), len(expected))
        for row in rows:
            for field in ['Name', 'Address', 'Pay information', 'Emergency contact']:
                self.assertEqual(row[field], expected[int(row['Record ID'])])

    def test_EH12_employee_csv_and_private_routes_deny_other_roles(self):
        self.assertEqual(self.client.get('/employees/export.csv').status_code, 302)
        for account in ['supervisor', 'employee']:
            client = self.login(account, self.app.test_client())
            self.assertEqual(client.get('/employees/export.csv').status_code, 403)
        self.login('supervisor')
        _, context = self.get_context('/employees')
        self.assertEqual({row['id'] for row in context['rows']}, {1, 3, 4})
        self.login('hr')
        for query in ['employee_id=1', 'team_id=1']:
            self.assertEqual(self.client.get('/employees/export.csv?' + query).status_code, 403)

    def test_EH13_team_week_reports_planned_hours_and_own_members(self):
        self.login('supervisor')
        _, context = self.get_context('/team-week?week=' + self.week)
        self.assertEqual({row['id'] for row in context['member_summaries']}, {1, 3, 4})
        for row in context['member_summaries']:
            self.assertEqual((row['working_days'], row['shift_count'], row['hours']), (5, 10, 40))
        self.assertEqual((context['total_shifts'], context['total_hours']), (30, 120))
        self.assertEqual(sum(context['daily_counts'].values()), 30)
        for offset in range(7):
            day = (date.fromisoformat(self.week) + timedelta(days=offset)).isoformat()
            people = 3 if offset < 5 else 0
            self.assertEqual(context['daily_people'][day], people)
            self.assertEqual(context['daily_counts'][day], people * 2)
            self.assertEqual(context['daily_shift_counts'][day],
                             {'Morning': people, 'Afternoon': people, 'Evening': 0})
        self.assertEqual(self.client.get('/team-week?team_id=2').status_code, 403)
        self.assertEqual(self.client.get('/team-week/print?team_id=2').status_code, 403)
        self.assertEqual(self.client.get('/team-week/print?team_id=1&team_id=2').status_code, 403)

    def test_EH14_invalid_week_reports_all_members_without_writing(self):
        self.login('supervisor')
        before = self.snapshot()
        captured = []

        def record(sender, template, context, **extra):
            captured.append(dict(context))

        with template_rendered.connected_to(record, self.app):
            response = self.client.post('/team-schedule', data={'csrf_token': self.csrf(), 'week': self.week, 'version': '1'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(set(captured[-1]['member_errors']), {1, 3, 4})
        self.assertTrue(all(captured[-1]['member_errors'][member] for member in [1, 3, 4]))
        self.assertEqual(str(captured[-1]['submitted_version']), '1')
        self.assertEqual(self.snapshot(), before)

    def test_EH15_previous_draft_get_does_not_overwrite_saved_target(self):
        source, target = '2030-01-07', '2030-01-14'
        self.save_fixture_week(source)
        self.save_fixture_week(target, shifts=('Afternoon', 'Evening'))
        self.write('UPDATE schedule_weeks SET version=4 WHERE team_id=1 AND week=?', (target,))
        self.login('supervisor')
        before = self.snapshot()
        _, context = self.get_context('/team-schedule?week=' + target + '&draft=previous')
        self.assertTrue(context['draft_loaded'])
        self.assertEqual(set(context['selected'][(1, target)]), {'Morning', 'Afternoon'})
        self.assertEqual(context['draft_source_week'], source)
        self.assertEqual(str(context['submitted_version']), '4')
        self.assertEqual(self.snapshot(), before)

    def test_EH16_draft_maps_current_members_and_leaves_new_member_blank(self):
        self.save_fixture_week('2030-01-07')
        self.write('UPDATE employees SET team_id=NULL WHERE id=3')
        self.write('UPDATE employees SET team_id=1 WHERE id=10')
        self.login('supervisor')
        before = self.snapshot()
        _, context = self.get_context('/team-schedule?week=2030-01-14&draft=previous')
        self.assertEqual({row['id'] for row in context['members']}, {1, 4, 10})
        self.assertTrue(all(not shifts for (member, unused), shifts in context['selected'].items() if member == 10))
        self.assertFalse(any(member == 3 for member, unused in context['selected']))
        self.assertIn(10, [row['id'] for row in context['draft_new_members']])
        self.assertEqual(self.snapshot(), before)
        self.write('UPDATE employees SET team_id=1 WHERE id=11')
        before = self.snapshot()
        form = self.schedule_form('2030-01-14', members=(1, 4, 10), draft_source='previous', draft_reviewed='1')
        _, rejected = self.get_context('/team-schedule', expected=422, method='post', data=form)
        self.assertIn(11, rejected['member_errors'])
        self.assertEqual(self.snapshot(), before)

    def test_EH17_draft_requires_review_then_complete_save(self):
        self.save_fixture_week('2030-01-07')
        self.login('supervisor')
        form = self.schedule_form('2030-01-14', draft_source='previous')
        before = self.snapshot()
        self.assertEqual(self.client.post('/team-schedule', data=form).status_code, 422)
        self.assertEqual(self.snapshot(), before)
        form['draft_reviewed'] = '1'
        self.assertEqual(self.client.post('/team-schedule', data=form).status_code, 302)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM assignments a JOIN schedule_weeks w ON w.id=a.schedule_id WHERE w.team_id=1 AND w.week='2030-01-14'"), 30)

    def test_EH18_old_draft_version_cannot_overwrite_concurrent_save(self):
        self.save_fixture_week('2030-01-07')
        self.save_fixture_week('2030-01-14')
        self.write("UPDATE schedule_weeks SET version=4 WHERE team_id=1 AND week='2030-01-14'")
        self.login('supervisor')
        _, context = self.get_context('/team-schedule?week=2030-01-14&draft=previous')
        old_version = context['submitted_version']
        self.write("UPDATE schedule_weeks SET version=version+1 WHERE team_id=1 AND week='2030-01-14'")
        before = self.snapshot()
        form = self.schedule_form('2030-01-14', old_version, draft_source='previous', draft_reviewed='1')
        for unused in range(2):
            response, rejected = self.get_context('/team-schedule', expected=422, method='post', data=form)
            self.assertEqual(str(rejected['submitted_version']), '4')
            self.assertEqual(hidden(response, 'version'), '4')
        self.assertEqual(self.snapshot(), before)

    def test_EH19_no_previous_week_is_explicit_empty_readonly_state(self):
        self.save_fixture_week('2031-01-13')
        self.login('supervisor')
        before = self.snapshot()
        _, context = self.get_context('/team-schedule?week=2031-01-13&draft=previous')
        self.assertTrue(context['draft_missing'])
        self.assertFalse(any(context['selected'].values()))
        self.assertEqual(self.snapshot(), before)

    def test_EH20_today_is_independent_of_selected_historical_week(self):
        self.save_fixture_week('2030-01-07')
        self.save_fixture_week('2029-12-24', shifts=('Afternoon', 'Evening'))
        self.login('employee')
        with patch('workspace.zhengzhou_now', return_value=self.fixed_now):
            _, context = self.get_context('/my-schedule?week=2029-12-24')
        self.assertEqual(str(context['today']), '2030-01-07')
        self.assertEqual(context['today_shifts'], ['Morning', 'Afternoon'])
        self.assertEqual(context['selected']['2029-12-24'], ['Afternoon', 'Evening'])
        self.assertEqual((context['working_days'], context['assignment_count'], context['total_hours']), (5, 10, 40))

    def test_EH21_next_shift_includes_exact_start_excludes_started_and_beyond_90_days(self):
        self.save_fixture_week('2030-01-07')
        self.login('employee')
        for now, shift in [(self.fixed_now.replace(hour=8), 'Morning'),
                           (self.fixed_now.replace(hour=8, second=1), 'Afternoon')]:
            with patch('workspace.zhengzhou_now', return_value=now):
                _, context = self.get_context('/my-schedule')
            self.assertEqual(context['next_shift']['shift'], shift)
        with patch('workspace.zhengzhou_now', return_value=self.fixed_now):
            _, context = self.get_context('/my-schedule')
        self.assertEqual(context['next_shift']['day'], '2030-01-07')
        self.assertEqual(context['next_shift']['shift'], 'Afternoon')
        self.write('DELETE FROM assignments WHERE employee_id=1')
        schedule = self.save_fixture_week('2030-04-08', members=())
        self.write('INSERT INTO assignments VALUES(?,?,?,?)', (schedule, 1, '2030-04-08', 'Morning'))
        with patch('workspace.zhengzhou_now', return_value=self.fixed_now):
            _, context = self.get_context('/my-schedule')
        self.assertIsNone(context['next_shift'])
        self.write('INSERT INTO assignments VALUES(?,?,?,?)', (schedule, 1, '2030-04-07', 'Afternoon'))
        with patch('workspace.zhengzhou_now', return_value=self.fixed_now):
            _, context = self.get_context('/my-schedule')
        self.assertEqual(context['next_shift']['day'], '2030-04-07')
        with patch('workspace.zhengzhou_now', return_value=self.fixed_now - timedelta(seconds=1)):
            _, context = self.get_context('/my-schedule')
        self.assertIsNone(context['next_shift'])

    def test_EH22_schedule_and_print_reject_other_identity_and_roles(self):
        self.login('employee')
        for route in ['/my-schedule', '/my-schedule/print']:
            self.assertEqual(self.client.get(route + '?employee_id=2').status_code, 403)
            self.assertEqual(self.client.get(route + '?employee_id=1&employee_id=2').status_code, 403)
            response, context = self.get_context(route)
            self.assertEqual(context['employee'].id, 1)
            self.assertNotIn('Chen Rui', response.get_data(as_text=True))
        for account, forbidden in [('hr', ['/team-week/print', '/my-schedule/print', '/my-checkins/print']),
                                    ('employee', ['/team-week/print']), ('supervisor', ['/my-schedule/print', '/my-checkins/print'])]:
            client = self.login(account, self.app.test_client())
            for route in forbidden:
                self.assertEqual(client.get(route).status_code, 403)

    def test_EH23_history_dates_include_endpoints_page_and_export_all(self):
        self.insert_checkins()
        self.login('employee')
        args = '?from=2030-01-01&to=2030-01-25'
        collected = []
        for page in [1, 2]:
            _, context = self.get_context('/my-checkins' + args + '&page=' + str(page))
            self.assertEqual(context['count'], 25)
            self.assertEqual(context['pages'], 2)
            collected.extend(row['recorded_at'] for row in context['rows'])
        self.assertEqual(collected, [f'2030-01-{i:02}T' +
                                    ('00:00:00' if i == 1 else '23:59:59' if i == 25 else '12:00:00') + '+08:00'
                                    for i in range(25, 0, -1)])
        response = self.client.get('/my-checkins/export.csv' + args + '&page=2')
        rows = self.csv_rows(response)
        self.assertEqual(response.headers['X-Total-Records'], '25')
        self.assertEqual(len(rows), 25)
        self.assertTrue(all(row['Employee record ID'] == '1' for row in rows))
        self.assertEqual([row['Recorded at (UTC+08:00)'] for row in rows], collected)
        _, context = self.get_context('/my-checkins?from=2030-01-10&to=2030-01-10')
        self.assertEqual(context['count'], 1)
        _, context = self.get_context('/my-checkins/print' + args)
        self.assertEqual([row['recorded_at'] for row in context['rows']], collected)
        _, context = self.get_context('/my-checkins?from=&to=')
        self.assertEqual(context['count'], 25)

    def test_EH24_history_invalid_dates_and_page_are_rejected(self):
        self.login('employee')
        for suffix in ['?from=not-a-date', '?to=2030-02-30', '?from=2030-01-20&to=2030-01-01', '?page=abc']:
            self.assertEqual(self.client.get('/my-checkins' + suffix).status_code, 400, suffix)
        for route in ['/my-checkins/export.csv', '/my-checkins/print']:
            self.assertEqual(self.client.get(route + '?from=2030-02-30').status_code, 400)

    def test_EH25_history_rejects_other_person_and_nonemployee(self):
        self.insert_checkins()
        self.login('employee')
        for route in ['/my-checkins', '/my-checkins/export.csv', '/my-checkins/print']:
            self.assertEqual(self.client.get(route + '?employee_id=2').status_code, 403)
            self.assertEqual(self.client.get(route + '?employee_id=1&employee_id=2').status_code, 403)
        _, context = self.get_context('/my-checkins')
        self.assertTrue(all(row['employee_id'] == 1 for row in context['rows']))
        for account in ['hr', 'supervisor']:
            client = self.login(account, self.app.test_client())
            for route in ['/my-checkins', '/my-checkins/export.csv', '/my-checkins/print']:
                self.assertEqual(client.get(route).status_code, 403)

    def test_EH26_all_enhancement_gets_are_readonly_and_schema_unchanged(self):
        self.insert_checkins()
        paths = {'hr': ['/dashboard', '/employees?sort=name_asc', '/employees/export.csv'],
                 'supervisor': ['/dashboard', '/employees', '/team-week', '/team-week/print', '/team-schedule?draft=previous'],
                 'employee': ['/dashboard', '/my-schedule', '/my-schedule/print', '/my-checkins',
                              '/my-checkins/export.csv', '/my-checkins/print']}
        for account, routes in paths.items():
            client = self.login(account, self.app.test_client())
            before = self.snapshot()
            for route in routes:
                self.assertEqual(client.get(route).status_code, 200, route)
            self.assertEqual(self.snapshot(), before, account)
        with closing(connect(self.database)) as db:
            self.assertEqual(list(db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")), self.schema)
            expected_columns = {
                'teams': ['id', 'name'],
                'employees': ['id', 'name', 'age', 'position', 'department', 'address', 'pay_info',
                              'emergency_contact', 'team_id', 'version'],
                'users': ['id', 'username', 'password_hash', 'role', 'employee_id', 'team_id'],
                'sessions': ['token', 'user_id', 'expires_at'],
                'schedule_weeks': ['id', 'team_id', 'week', 'version'],
                'assignments': ['schedule_id', 'employee_id', 'day', 'shift'],
                'checkins': ['id', 'employee_id', 'operation_id', 'recorded_at'],
                'login_failures': ['source', 'username', 'attempted_at'],
                'settings': ['key', 'value']}
            self.assertEqual({row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}, set(expected_columns))
            for table, columns in expected_columns.items():
                self.assertEqual([row['name'] for row in db.execute('PRAGMA table_info(' + table + ')')], columns)


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.evidence = []

    def record(self, test, status):
        name = getattr(test, '_testMethodName', str(test))
        self.evidence.append({'id': name.split('_')[1] if '_' in name else name, 'name': name, 'status': status})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, 'PASS')

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.record(test, 'FAIL')

    def addError(self, test, error):
        super().addError(test, error)
        self.record(test, 'ERROR')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(EnhancementTests))
    source_files = [ROOT / 'app.py', ROOT / 'models.py', ROOT / 'workspace.py', Path(__file__)]
    report = {'release': '2.1.0-flask', 'recorded_at': datetime.now(ZHENGZHOU_TIMEZONE).isoformat(timespec='seconds'),
              'evidence_type': 'Flask test client and temporary SQLite database; controlled UTC+08:00 clock boundaries',
              'runtime_database_used': False, 'total': result.testsRun,
              'passed': sum(row['status'] == 'PASS' for row in result.evidence),
              'failures': len(result.failures), 'errors': len(result.errors), 'successful': result.wasSuccessful(),
              'environment': {'python': platform.python_version(), 'system': platform.platform()},
              'source_sha256': {p.relative_to(ROOT.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in source_files if p.is_file()},
              'results': result.evidence,
              'not_established': ['independent GUI or cross-group acceptance', 'physical phone',
                                  'real store presence or WAN/TLS', '500 concurrent users', 'teacher acceptance']}
    output = ROOT.parent / 'evidence/website_enhancement_results.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
