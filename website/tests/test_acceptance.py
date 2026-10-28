r"""可重复的自动化支持证据。测试临时数据库，不修改课堂演示数据库。

运行：.venv\Scripts\python.exe tests\test_acceptance.py
本文件的测试不是老师验收，也不替代另一组执行 GUI Test Plan。
"""
import html
import io
import json
import platform
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash
from itsdangerous import URLSafeTimedSerializer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import RELEASE, create_app
from database import connect
from manage import main as manage_main
from models import ScheduleWeek, Team, ValidationError, ZHENGZHOU_TIMEZONE, zhengzhou_today


def hidden(response, name):
    pattern = r'name="' + name + r'" value="([^"]*)"'
    match = re.search(pattern, response.get_data(as_text=True))
    if not match:
        raise AssertionError('Missing hidden field: ' + name)
    return html.unescape(match.group(1))


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.database = Path(cls.temp.name) / 'test.sqlite'
        cls.app = create_app({'TESTING': True, 'DATABASE': str(cls.database), 'SECRET_KEY': 'isolated-test-secret'})
        with closing(connect(cls.database)) as db:
            cls.baseline = sqlite3.connect(':memory:')
            db.backup(cls.baseline)

    @classmethod
    def tearDownClass(cls):
        cls.baseline.close()
        cls.temp.cleanup()

    def setUp(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.baseline.backup(db)
        self.client = self.app.test_client()
        today = zhengzhou_today()
        self.week = (today - timedelta(days=today.weekday())).isoformat()

    def login(self, username='hr', client=None):
        client = client or self.client
        token = hidden(client.get('/login'), 'csrf_token')
        return client.post('/login', data={'csrf_token': token, 'username': username, 'password': 'Demo!2026'})

    def csrf(self, client=None):
        with (client or self.client).session_transaction() as session:
            return session['csrf']

    def scalar(self, sql, params=()):
        with closing(connect(self.database)) as db:
            return db.execute(sql, params).fetchone()[0]

    def schedule_form(self, day_count=5):
        form = MultiDict({'csrf_token': self.csrf(), 'week': self.week, 'version': '1'})
        for employee_id in (1, 3, 4):
            for offset in range(day_count):
                day = (date.fromisoformat(self.week) + timedelta(days=offset)).isoformat()
                form.setlist(f'shift_{employee_id}_{day}', ['Morning', 'Afternoon'])
        return form

    def test_TC01_valid_login_and_logout(self):
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get('/employees').status_code, 200)
        self.client.post('/logout', data={'csrf_token': self.csrf()})
        self.assertEqual(self.client.get('/employees').status_code, 302)

    def test_TC02_invalid_login(self):
        token = hidden(self.client.get('/login'), 'csrf_token')
        response = self.client.post('/login', data={'csrf_token': token, 'username': 'hr', 'password': 'wrong'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sessions'), 0)

    def test_TC03_csrf_rejected(self):
        self.login()
        self.assertEqual(self.client.post('/employees/new', data={'name': 'Invalid'}).status_code, 400)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 500)

    def test_TC04_create_name_only_and_duplicate(self):
        self.login()
        for unused in range(2):
            self.assertEqual(self.client.post('/employees/new', data={'csrf_token': self.csrf(), 'name': 'Same Name'}).status_code, 302)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM employees WHERE name='Same Name'"), 2)

    def test_TC05_invalid_input(self):
        self.login()
        for values in ({'name': '   '}, {'name': 'Test', 'age': '-1'}, {'name': 'Test', 'age': '1.5'}, {'name': 'Test', 'age': 'abc'}):
            self.assertEqual(self.client.post('/employees/new', data={'csrf_token': self.csrf(), **values}).status_code, 422)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 500)

    def test_TC06_seven_groups_and_pay_text(self):
        self.login()
        values = dict(name='Seven Fields', age='0', position='Cashier', department='Retail', address='Test address', pay_info='Monthly salary agreed separately', emergency_contact='Test contact')
        response = self.client.post('/employees/new', data={'csrf_token': self.csrf(), **values}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        for value in values.values():
            self.assertIn(value, response.get_data(as_text=True))

    def test_TC07_name_search_and_empty(self):
        self.login()
        response = self.client.get('/employees?name=Alex+Chen')
        self.assertEqual(response.get_data(as_text=True).count('View details'), 2)
        self.assertIn('No employees match', self.client.get('/employees?name=NotARealPerson').get_data(as_text=True))

    def test_TC08_update_and_stale_version(self):
        self.login()
        form = {'csrf_token': self.csrf(), 'name': 'Updated', 'version': '1'}
        self.assertEqual(self.client.post('/employees/10/edit', data=form).status_code, 302)
        self.assertEqual(self.client.post('/employees/10/edit', data={**form, 'name': 'Stale'}).status_code, 422)
        self.assertEqual(self.scalar('SELECT name FROM employees WHERE id=10'), 'Updated')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 500)

    def test_TC09_update_failure_preserves_record(self):
        self.login()
        self.assertEqual(self.client.post('/employees/10/edit', data={'csrf_token': self.csrf(), 'name': '', 'version': '1'}).status_code, 422)
        self.assertEqual(self.scalar('SELECT name FROM employees WHERE id=10'), 'Demo Employee 010')

    def test_TC10_delete_cancellation_and_confirmation(self):
        self.login()
        self.assertEqual(self.client.get('/employees/10/delete').status_code, 200)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 500)
        self.assertEqual(self.client.post('/employees/10/delete', data={'csrf_token': self.csrf(), 'confirmation': 'wrong', 'version': 1}).status_code, 422)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 500)

    def test_TC11_delete_revokes_all_accounts_and_sessions(self):
        employee_client = self.app.test_client()
        self.login('employee', employee_client)
        with closing(connect(self.database)) as db, db:
            db.execute("INSERT INTO users(username,password_hash,role,employee_id) VALUES ('second',?,'employee',1)", (generate_password_hash('Demo!2026'),))
        second = self.app.test_client()
        self.login('second', second)
        self.login()
        response = self.client.post('/employees/1/delete', data={'csrf_token': self.csrf(), 'confirmation': 'Lin Wei', 'version': 1})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/employees/1').status_code, 404)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 499)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM users WHERE employee_id=1'), 0)
        self.assertEqual(employee_client.get('/my-schedule').status_code, 302)
        self.assertEqual(second.get('/my-schedule').status_code, 302)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM assignments WHERE employee_id=1'), 0)
        self.assertEqual(self.client.post('/employees/1/delete', data={'csrf_token': self.csrf()}).status_code, 404)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 499)

    def test_TC12_supervisor_scope_and_private_data(self):
        self.login('supervisor')
        response = self.client.get('/employees').get_data(as_text=True)
        self.assertIn('Lin Wei', response)
        self.assertNotIn('Chen Rui', response)
        self.assertNotIn('Fictional contact', response)
        self.assertEqual(self.client.get('/employees/1').status_code, 403)
        self.assertEqual(self.client.get('/employees/new').status_code, 403)

    def test_TC13_employee_restrictions(self):
        self.login('employee')
        for path in ['/employees', '/employees/1', '/team-schedule']:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.get('/my-schedule?employee_id=2').status_code, 403)

    def test_TC14_valid_schedule_revision(self):
        self.login('supervisor')
        form = self.schedule_form()
        key = f'shift_1_{self.week}'
        form.setlist(key, ['Afternoon', 'Evening'])
        self.assertEqual(self.client.post('/team-schedule', data=form).status_code, 302)
        self.assertEqual(self.scalar('SELECT version FROM schedule_weeks WHERE team_id=1 AND week=?', (self.week,)), 2)
        employee = self.app.test_client()
        self.login('employee', employee)
        self.assertIn('Evening', employee.get('/my-schedule?week=' + self.week).get_data(as_text=True))

    def test_TC15_incomplete_schedule_is_atomic(self):
        self.login('supervisor')
        for day_count in (4, 6):
            self.assertEqual(self.client.post('/team-schedule', data=self.schedule_form(day_count)).status_code, 422)
        self.assertEqual(self.scalar('SELECT version FROM schedule_weeks WHERE team_id=1 AND week=?', (self.week,)), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM assignments WHERE schedule_id=1'), 30)

    def test_TC16_invalid_or_duplicate_shifts(self):
        self.login('supervisor')
        for shifts in (['Morning'], ['Morning', 'Morning'], ['Morning', 'Invalid'], ['Morning', 'Afternoon', 'Evening']):
            form = self.schedule_form()
            form.setlist(f'shift_1_{self.week}', shifts)
            self.assertEqual(self.client.post('/team-schedule', data=form).status_code, 422)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM assignments WHERE schedule_id=1'), 30)

    def test_TC17_other_team_denied(self):
        self.login('supervisor')
        self.assertEqual(self.client.get('/team-schedule?team_id=2').status_code, 403)
        form = self.schedule_form()
        form.setlist(f'shift_2_{self.week}', ['Morning', 'Afternoon'])
        self.assertEqual(self.client.post('/team-schedule', data=form).status_code, 422)

    def test_TC18_stale_schedule_and_new_week(self):
        self.login('supervisor')
        form = self.schedule_form()
        self.assertEqual(self.client.post('/team-schedule', data=form).status_code, 302)
        self.assertEqual(self.client.post('/team-schedule', data=form).status_code, 422)
        new_week = (date.fromisoformat(self.week) + timedelta(days=28)).isoformat()
        form = self.schedule_form()
        form['week'], form['version'] = new_week, '0'
        for key in list(form):
            if key.startswith('shift_'):
                new_day = date.fromisoformat(key[-10:]) + timedelta(days=28)
                form.setlist(key[:-10] + new_day.isoformat(), form.getlist(key))
                del form[key]
        self.assertEqual(self.client.post('/team-schedule', data=form).status_code, 302)

    def test_TC19_personal_empty_and_remote_view(self):
        self.login('employee')
        self.assertEqual(self.client.get('/my-schedule', environ_overrides={'REMOTE_ADDR': '203.0.113.10'}).status_code, 200)
        self.assertIn('No schedule for this week', self.client.get('/my-schedule?week=2030-01-07').get_data(as_text=True))
        self.assertEqual(self.client.post('/my-schedule', data={'csrf_token': self.csrf()}).status_code, 405)

    def test_TC20_onsite_checkin_and_retry(self):
        self.login('employee')
        operation = hidden(self.client.get('/check-in'), 'operation_id')
        form = {'csrf_token': self.csrf(), 'operation_id': operation}
        for unused in range(2):
            self.assertEqual(self.client.post('/check-in', data=form).status_code, 302)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM checkins'), 1)
        self.assertEqual(self.scalar('SELECT employee_id FROM checkins'), 1)

    def test_TC21_remote_checkin_and_spoof_denied(self):
        self.login('employee')
        operation = hidden(self.client.get('/check-in'), 'operation_id')
        response = self.client.post('/check-in', data={'csrf_token': self.csrf(), 'operation_id': operation},
                                    environ_overrides={'REMOTE_ADDR': '203.0.113.10'}, headers={'X-Forwarded-For': '127.0.0.1'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM checkins'), 0)

    def test_TC22_checkin_identity_and_token(self):
        self.login('employee')
        operation = hidden(self.client.get('/check-in'), 'operation_id')
        other = self.app.test_client()
        self.login('employee2', other)
        self.assertEqual(other.post('/check-in', data={'csrf_token': self.csrf(other), 'operation_id': operation}).status_code, 403)
        self.assertEqual(self.client.post('/check-in', data={'csrf_token': self.csrf(), 'operation_id': 'forged'}).status_code, 400)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM checkins'), 0)

    def test_TC23_persistence_after_application_restart(self):
        self.login()
        self.client.post('/employees/new', data={'csrf_token': self.csrf(), 'name': 'Persistent'})
        self.client.post('/employees/10/delete', data={'csrf_token': self.csrf(), 'confirmation': 'Demo Employee 010', 'version': 1})
        restarted = create_app({'TESTING': True, 'DATABASE': str(self.database), 'SECRET_KEY': 'isolated-test-secret'})
        client = restarted.test_client()
        self.login(client=client)
        self.assertIn('Persistent', client.get('/employees?name=Persistent').get_data(as_text=True))
        self.assertEqual(client.get('/employees/10').status_code, 404)

    def test_TC24_optional_filters_and_group_counts(self):
        self.login()
        response = self.client.get('/employees?name=Demo&department=Retail&position=Store+Assistant').get_data(as_text=True)
        self.assertIn('248', response)
        with closing(connect(self.database)) as db:
            totals = db.execute('SELECT COUNT(*) FROM employees GROUP BY department').fetchall()
            self.assertEqual(sum(row[0] for row in totals), 500)

    def test_TC25_sql_and_html_input(self):
        self.login()
        self.assertEqual(self.client.get('/employees?name=%27+OR+1=1--').status_code, 200)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 500)
        response = self.client.post('/employees/new', data={'csrf_token': self.csrf(), 'name': '<script>alert(1)</script>'}, follow_redirects=True).get_data(as_text=True)
        self.assertIn('&lt;script&gt;', response)
        self.assertNotIn('<script>', response)

    def test_TC26_no_javascript_or_plaintext_passwords(self):
        response = self.client.get('/login')
        self.assertIn("script-src 'none'", response.headers['Content-Security-Policy'])
        for path in list((ROOT/'templates').glob('*.html')) + list((ROOT/'static').glob('*')):
            self.assertNotIn('<script', path.read_text(encoding='utf-8').lower())
            self.assertNotIn('.js', path.name)
        self.assertNotEqual(self.scalar("SELECT password_hash FROM users WHERE username='hr'"), 'Demo!2026')

    def test_TC27_failed_save_is_not_reported_as_success(self):
        self.login()
        with patch('models.Employee.create', side_effect=sqlite3.OperationalError('controlled failure')):
            response = self.client.post('/employees/new', data={'csrf_token': self.csrf(), 'name': 'Should Not Save'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Employee saved.', response.get_data(as_text=True))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 500)

    def test_TC28_bad_dates_and_missing_records(self):
        self.login('supervisor')
        self.assertEqual(self.client.get('/team-schedule?week=not-a-date').status_code, 400)
        self.assertEqual(self.client.get('/team-schedule?week=2026-10-28').status_code, 200)

    def test_TC29_malformed_session_and_unicode_csrf_are_rejected(self):
        self.login()
        with self.client.session_transaction() as current:
            current['sid'] = {'invalid': 'type'}
            current['csrf'] = ['invalid']
        self.assertEqual(self.client.get('/employees').status_code, 302)
        with self.client.session_transaction() as current:
            self.assertNotIn('sid', current)
            self.assertIsInstance(current['csrf'], str)
        response = self.client.post('/login', data={'csrf_token': '非ASCII', 'username': 'hr', 'password': 'Demo!2026'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Traceback', response.get_data(as_text=True))

    def test_TC30_relogin_revokes_previous_cookie_session(self):
        self.login()
        previous_cookie = self.client.get_cookie('session').value
        with self.client.session_transaction() as current:
            previous_sid = current['sid']
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sessions WHERE token=?', (previous_sid,)), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sessions'), 1)
        old_client = self.app.test_client()
        old_client.set_cookie('session', previous_cookie)
        self.assertEqual(old_client.get('/employees').status_code, 302)
        self.assertEqual(self.client.get('/employees').status_code, 200)

    def test_TC31_signed_malformed_operation_and_identity_override(self):
        self.login('employee')
        signer = URLSafeTimedSerializer(self.app.secret_key, salt='check-in')
        for payload in (None, [], {'employee_id': 1, 'nonce': []},
                        {'employee_id': True, 'nonce': 'a' * 22},
                        {'employee_id': 1, 'nonce': ''}, {'employee_id': 1, 'nonce': 2}):
            with self.subTest(payload=payload):
                response = self.client.post('/check-in', data={'csrf_token': self.csrf(), 'operation_id': signer.dumps(payload)})
                self.assertEqual(response.status_code, 400)
        operation = hidden(self.client.get('/check-in'), 'operation_id')
        self.assertEqual(self.client.post('/check-in', data={'csrf_token': self.csrf(), 'operation_id': operation,
                                                           'employee_id': '2'}).status_code, 403)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM checkins'), 0)

    def test_TC32_account_cli_rejects_mixed_role_scope_and_bad_rows(self):
        cases = [('--role', 'hr', '--employee-id', '1'),
                 ('--role', 'hr', '--team-id', '1'),
                 ('--role', 'supervisor', '--team-id', '1', '--employee-id', '1'),
                 ('--role', 'employee', '--employee-id', '1', '--team-id', '2'),
                 ('--role', 'employee', '--employee-id', '9999'),
                 ('--role', 'supervisor', '--team-id', '9999')]
        for parameters in cases:
            with self.subTest(parameters=parameters), redirect_stderr(io.StringIO()), \
                    patch('manage.getpass.getpass') as password_prompt:
                with self.assertRaises(SystemExit):
                    manage_main(['create-account', '--username', 'invalid', *parameters], self.database)
                password_prompt.assert_not_called()
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM users'), 5)
        with closing(connect(self.database)) as db, db:
            user_id = db.execute("INSERT INTO users(username,password_hash,role,employee_id,team_id) VALUES ('mixed',?,'employee',1,2)",
                                 (generate_password_hash('Demo!2026'),)).lastrowid
            db.execute('INSERT INTO sessions VALUES (?,?,?)', ('invalid-role-scope', user_id, 9999999999))
        self.assertEqual(self.login('mixed').status_code, 401)
        with self.client.session_transaction() as current:
            current['sid'] = 'invalid-role-scope'
        self.assertEqual(self.client.get('/my-schedule').status_code, 302)

    def test_TC33_team_assignment_cli_is_consistent_and_revokes_sessions(self):
        output = io.StringIO()
        with redirect_stdout(output), patch('manage.getpass.getpass', return_value='Demo!2026'):
            manage_main(['create-account', '--username', 'new-employee', '--role', 'employee', '--employee-id', '10'], self.database)
        self.assertNotIn('Demo!2026', output.getvalue())
        employee_client = self.app.test_client()
        self.assertEqual(self.login('new-employee', employee_client).status_code, 302)
        with redirect_stdout(io.StringIO()):
            manage_main(['assign-team', '--employee-id', '10', '--team-id', '2'], self.database)
        self.assertEqual(self.scalar('SELECT team_id FROM employees WHERE id=10'), 2)
        self.assertIsNone(self.scalar("SELECT team_id FROM users WHERE username='new-employee'"))
        self.assertEqual(employee_client.get('/my-schedule').status_code, 302)
        for parameters in (['--employee-id', '1', '--team-id', '2'], ['--employee-id', '10', '--team-id', '9999']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                manage_main(['assign-team', *parameters], self.database)
        self.assertEqual(self.scalar('SELECT team_id FROM employees WHERE id=1'), 1)

    def test_TC34_mid_schedule_sql_failure_rolls_back_deleted_assignments(self):
        self.login('supervisor')
        with closing(connect(self.database)) as db, db:
            before = [tuple(row) for row in db.execute('SELECT * FROM assignments ORDER BY schedule_id,employee_id,day,shift')]
            db.execute("""CREATE TRIGGER reject_partial_schedule BEFORE INSERT ON assignments
                WHEN NEW.schedule_id=1 AND NEW.employee_id=3 AND NEW.shift='Afternoon'
                BEGIN SELECT RAISE(ABORT, 'controlled failure after old rows were deleted'); END""")
        trace = []

        def traced_connection(path):
            db = connect(path)
            db.set_trace_callback(trace.append)
            return db

        with patch('app.connect', side_effect=traced_connection):
            response = self.client.post('/team-schedule', data=self.schedule_form())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('The complete team week was saved.', response.get_data(as_text=True))
        statements = [statement.strip().upper() for statement in trace]
        self.assertIn('BEGIN IMMEDIATE', statements)
        self.assertTrue(any(statement.startswith('DELETE FROM ASSIGNMENTS') for statement in statements))
        self.assertGreater(sum(statement.startswith('INSERT INTO ASSIGNMENTS') for statement in statements), 10)
        self.assertIn('ROLLBACK', statements)
        self.assertEqual(self.scalar('SELECT version FROM schedule_weeks WHERE id=1'), 1)
        with closing(connect(self.database)) as db:
            after = [tuple(row) for row in db.execute('SELECT * FROM assignments ORDER BY schedule_id,employee_id,day,shift')]
        self.assertEqual(after, before)
        with self.client.session_transaction() as current:
            self.assertNotIn('_flashes', current)

    def test_TC35_restart_retains_updates_schedules_and_checkins(self):
        self.login()
        values = {'csrf_token': self.csrf(), 'name': 'Persisted Update', 'age': '0', 'position': 'Revised position',
                  'department': 'Revised department', 'address': 'Updated address', 'pay_info': 'Agreed pay text',
                  'emergency_contact': 'Updated contact', 'version': '1'}
        self.assertEqual(self.client.post('/employees/10/edit', data=values).status_code, 302)
        supervisor = self.app.test_client()
        self.login('supervisor', supervisor)
        form = self.schedule_form()
        form['csrf_token'] = self.csrf(supervisor)
        form.setlist(f'shift_1_{self.week}', ['Afternoon', 'Evening'])
        self.assertEqual(supervisor.post('/team-schedule', data=form).status_code, 302)
        employee = self.app.test_client()
        self.login('employee', employee)
        operation = hidden(employee.get('/check-in'), 'operation_id')
        self.assertEqual(employee.post('/check-in', data={'csrf_token': self.csrf(employee), 'operation_id': operation}).status_code, 302)
        timestamp = self.scalar('SELECT recorded_at FROM checkins WHERE employee_id=1')
        restarted = create_app({'TESTING': True, 'DATABASE': str(self.database), 'SECRET_KEY': 'isolated-test-secret'})
        hr = restarted.test_client()
        self.login(client=hr)
        detail = hr.get('/employees/10').get_data(as_text=True)
        for value in ('Persisted Update', 'Revised position', 'Updated address', 'Agreed pay text', 'Updated contact'):
            self.assertIn(value, detail)
        staff = restarted.test_client()
        self.login('employee', staff)
        self.assertIn('Evening', staff.get('/my-schedule?week=' + self.week).get_data(as_text=True))
        self.assertIn(timestamp, staff.get('/check-in').get_data(as_text=True))
        self.assertEqual(staff.post('/check-in', data={'csrf_token': self.csrf(staff), 'operation_id': operation}).status_code, 302)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM checkins WHERE employee_id=1'), 1)

    def test_TC36_zhengzhou_date_controls_default_and_seed_week(self):
        boundary = datetime(2026, 11, 2, 0, 30, tzinfo=ZHENGZHOU_TIMEZONE)
        self.login('supervisor')
        with patch('models.datetime') as clock:
            clock.now.return_value = boundary
            self.assertEqual(zhengzhou_today(), date(2026, 11, 2))
            response = self.client.get('/team-schedule')
            self.assertIn('2026-11-02 — 2026-11-08', response.get_data(as_text=True))
            clock.now.assert_called_with(ZHENGZHOU_TIMEZONE)
            other_database = Path(self.temp.name) / 'timezone.sqlite'
            create_app({'TESTING': True, 'DATABASE': str(other_database), 'SECRET_KEY': 'isolated-timezone-secret'})
        with closing(connect(other_database)) as db:
            weeks = [row[0] for row in db.execute('SELECT DISTINCT week FROM schedule_weeks ORDER BY week')]
        self.assertEqual(weeks, ['2026-11-02', '2026-11-09'])

    def test_TC37_employee_delete_cascades_checkin_and_all_linked_data(self):
        employee = self.app.test_client()
        self.login('employee', employee)
        operation = hidden(employee.get('/check-in'), 'operation_id')
        self.assertEqual(employee.post('/check-in', data={'csrf_token': self.csrf(employee), 'operation_id': operation}).status_code, 302)
        with closing(connect(self.database)) as db, db:
            user_id = db.execute("INSERT INTO users(username,password_hash,role,employee_id) VALUES ('linked-user',?,'employee',1)",
                                 (generate_password_hash('Demo!2026'),)).lastrowid
            db.execute('INSERT INTO sessions VALUES (?,?,?)', ('linked-session', user_id, 9999999999))
        self.login()
        self.assertEqual(self.client.post('/employees/1/delete', data={'csrf_token': self.csrf(), 'confirmation': 'Lin Wei', 'version': '1'}).status_code, 302)
        for table in ('users', 'assignments', 'checkins'):
            self.assertEqual(self.scalar(f'SELECT COUNT(*) FROM {table} WHERE employee_id=1'), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sessions WHERE user_id=?', (user_id,)), 0)
        with closing(connect(self.database)) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_TC38_stale_team_membership_rejects_save_without_changes(self):
        self.login('supervisor')
        with closing(connect(self.database)) as db:
            members = Team(1, 'Retail Operations').members(db)
            before = [tuple(row) for row in db.execute('SELECT * FROM assignments ORDER BY schedule_id,employee_id,day,shift')]
        with closing(connect(self.database)) as db, db:
            db.execute('UPDATE employees SET team_id=1 WHERE id=10')
        days = [(date.fromisoformat(self.week) + timedelta(days=offset)).isoformat() for offset in range(7)]
        with closing(connect(self.database)) as db:
            with self.assertRaisesRegex(ValidationError, 'Team membership changed'):
                ScheduleWeek(1, self.week).save(db, members, days, self.schedule_form(), 1)
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM assignments ORDER BY schedule_id,employee_id,day,shift')], before)
        self.assertEqual(self.scalar('SELECT version FROM schedule_weeks WHERE id=1'), 1)

    def test_TC39_password_reset_revokes_existing_sessions(self):
        self.login('employee')
        with redirect_stdout(io.StringIO()), patch('manage.getpass.getpass', return_value='Changed!2026'):
            manage_main(['set-password', '--username', 'employee'], self.database)
        self.assertEqual(self.client.get('/my-schedule').status_code, 302)
        self.assertEqual(self.login('employee').status_code, 401)
        self.assertEqual(self.client.post('/login', data={'csrf_token': self.csrf(), 'username': 'employee', 'password': 'Changed!2026'}).status_code, 302)
        self.assertNotEqual(self.scalar("SELECT password_hash FROM users WHERE username='employee'"), 'Changed!2026')

    def test_TC40_direct_role_mutations_and_boundary_dates_are_denied(self):
        self.login('employee')
        for path in ('/employees/new', '/employees/1/edit', '/employees/1/delete', '/team-schedule'):
            self.assertEqual(self.client.post(path, data={'csrf_token': self.csrf(), 'name': 'Forbidden', 'version': '1'}).status_code, 403)
        self.login('supervisor')
        for path in ('/employees/new', '/employees/1/edit', '/employees/1/delete', '/check-in'):
            self.assertEqual(self.client.post(path, data={'csrf_token': self.csrf(), 'name': 'Forbidden', 'version': '1'}).status_code, 403)
        for week in ('0001-01-01', '9999-12-31', 'not-a-date'):
            self.assertEqual(self.client.get('/team-schedule?week=' + week).status_code, 400)
        self.login()
        for path in ('/team-schedule', '/check-in'):
            self.assertEqual(self.client.post(path, data={'csrf_token': self.csrf()}).status_code, 403)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM employees'), 500)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM checkins'), 0)


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.evidence = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.evidence.append({'id': test._testMethodName.split('_')[1], 'name': test._testMethodName, 'status': 'PASS'})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.evidence.append({'id': test._testMethodName.split('_')[1], 'name': test._testMethodName, 'status': 'FAIL'})

    def addError(self, test, error):
        super().addError(test, error)
        name = getattr(test, '_testMethodName', str(test))
        self.evidence.append({'id': name.split('_')[1] if '_' in name else name, 'name': name, 'status': 'ERROR'})


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AcceptanceTests)
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    out = ROOT.parent / 'evidence'
    out.mkdir(exist_ok=True)
    (out/'automated_results.json').write_text(json.dumps({
        'release': RELEASE, 'evidence_type': 'Flask test client and SQLite checks; simulated network sources',
        'total': result.testsRun, 'passed': result.wasSuccessful(), 'results': result.evidence,
        'not_established': ['teacher acceptance', 'independent cross-group GUI testing', 'real workplace network verification']
    }, indent=2), encoding='utf-8')
    (out/'website_security_results.json').write_text(json.dumps({
        'release': RELEASE,
        'recorded_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
        'environment': {'python': platform.python_version(), 'system': platform.platform()},
        'command': '.venv\\Scripts\\python.exe tests\\test_acceptance.py',
        'evidence_type': 'Temporary SQLite databases and Flask test client; simulated direct source IP addresses',
        'runtime_database_used': False, 'total': result.testsRun,
        'passed': len([item for item in result.evidence if item['status'] == 'PASS']),
        'failures': len(result.failures), 'errors': len(result.errors), 'successful': result.wasSuccessful(),
        'changes': ['Reject malformed signed session/CSRF and check-in operation payloads',
                    'Reauthentication and password reset revoke previous server sessions',
                    'Role-specific account scopes and installation team changes are checked atomically',
                    'Schedule member validation occurs under the same write lock as the complete save',
                    'Default and seed weeks use Zhengzhou UTC+8 date',
                    'Boundary dates return a clear input error'],
        'key_evidence': {'mid_save_rollback': 'TC34: real SQLite trigger aborts after DELETE and partial INSERT; trace contains BEGIN IMMEDIATE, DELETE, INSERT and ROLLBACK; every prior assignment and version restored',
                         'restart_persistence': 'TC23 and TC35: create/delete, seven-field update, saved schedule revision, check-in and retry compared after create_app reinitialization',
                         'physical_delete': 'TC11 and TC37: employee, linked accounts, sessions, assignments and check-ins removed; no foreign-key orphans'},
        'results': result.evidence,
        'not_established': ['teacher acceptance', 'independent cross-group GUI testing', 'real workplace network verification',
                            'external remote access deployment', '500 concurrent users']
    }, indent=2), encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() else 1)
