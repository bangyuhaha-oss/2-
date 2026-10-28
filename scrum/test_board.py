"""隔离看板回归：所有任务、事件、密钥和证据夹具都位于临时目录。"""
import csv
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from board import EVENT_FIELDS, ROOT, TASK_FIELDS, create_app


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='group2-board-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.delivery = Path(self.temporary.name) / 'delivery'
        self.source = self.delivery / 'scrum'
        self.source.mkdir(parents=True)
        (self.delivery / 'evidence').mkdir()
        (self.delivery / 'evidence' / 'actual_check.txt').write_text('Isolated synthetic test evidence.', encoding='utf-8')
        self.task = ['T01', 'Synthetic task', 'A test-only story.', 'Use local evidence.', 'Test-only', '梁邦羽', 'Doing', '']
        self.legacy = ['1', '2026-10-26T00:00:00+08:00', 'T01', 'Synthetic imported actor', '', 'Doing', 'Synthetic historical fixture; not real team history.']
        for name, fields, row in [('tasks', TASK_FIELDS, self.task), ('events', EVENT_FIELDS[:7], self.legacy)]:
            with (self.source / f'{name}.csv').open('w', encoding='utf-8', newline='') as file:
                writer = csv.writer(file); writer.writerow(fields); writer.writerow(row)
        self.config = {'TESTING': True, 'DATABASE': str(self.source / 'test.sqlite'),
                       'DELIVERY_ROOT': self.delivery, 'IMPORT_DIR': self.source,
                       'EXPORT_DIR': self.source, 'SECRET_KEY': 'isolated-test-key'}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.token = re.search(rb'name="csrf_token" value="([^"]+)"', self.client.get('/').data)[1].decode()

    def rows(self, table):
        with closing(sqlite3.connect(self.config['DATABASE'])) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY id')]

    def post(self, **changes):
        data = {'csrf_token': self.token, 'status': 'Review / Test', 'owner': '李远向',
                'operator': 'Isolated test operator', 'evidence': '', 'note': 'An actual isolated test update.'}
        data.update(changes)
        return self.client.post('/tasks/T01', data=data)

    def test_csrf_required_on_every_post_and_no_state_change(self):
        before = (self.rows('tasks'), self.rows('events'))
        for token in ['', 'wrong-token']:
            self.assertEqual(self.post(csrf_token=token).status_code, 400)
            self.assertEqual(self.client.post('/unknown', data={'csrf_token': token}).status_code, 400)
        self.assertEqual((self.rows('tasks'), self.rows('events')), before)

    def test_unicode_csrf_rejected_and_malformed_session_token_repaired(self):
        before = (self.rows('tasks'), self.rows('events'))
        self.assertEqual(self.post(csrf_token='中文').status_code, 400)
        for malformed in [None, 42, 'short', '中' * 43, 'x' * 129]:
            with self.subTest(session_token=malformed):
                with self.client.session_transaction() as session:
                    session['csrf'] = malformed
                self.assertEqual(self.post(csrf_token='中文').status_code, 400)
                with self.client.session_transaction() as session:
                    repaired = session['csrf']
                self.assertIsInstance(repaired, str)
                self.assertTrue(repaired.isascii())
                self.assertTrue(32 <= len(repaired) <= 128)
                self.assertEqual((self.rows('tasks'), self.rows('events')), before)
        self.assertEqual(self.post(csrf_token=repaired).status_code, 302)

    def test_operator_is_separate_from_proposed_owner_and_timestamp_current(self):
        before = datetime.now().astimezone().timestamp()
        self.assertEqual(self.post().status_code, 302)
        event = self.rows('events')[-1]
        self.assertEqual(event['actor'], 'Isolated test operator')
        self.assertEqual(event['actor_kind'], 'self-reported-local')
        self.assertEqual((event['old_owner'], event['new_owner']), ('梁邦羽', '李远向'))
        self.assertGreaterEqual(datetime.fromisoformat(event['at']).timestamp(), before - 1)
        self.assertEqual(event['old_status'], 'Doing')
        self.assertEqual(event['new_status'], 'Review / Test')

    def test_done_rejects_text_missing_directory_absolute_and_escape_evidence(self):
        outside = Path(self.temporary.name) / 'outside.txt'; outside.write_text('outside', encoding='utf-8')
        before = (self.rows('tasks'), self.rows('events'))
        for value in ['', 'all tests passed', 'evidence/missing.txt', 'evidence', '../outside.txt',
                      str(outside), 'C:\\outside.txt', 'evidence/actual_check.txt; all tests passed']:
            with self.subTest(evidence=value):
                response = self.post(status='Done', evidence=value)
                self.assertEqual(response.status_code, 400)
                self.assertEqual((self.rows('tasks'), self.rows('events')), before)

    def test_done_existing_file_persists_restart_and_csv_snapshots(self):
        self.assertEqual(self.post(status='Done', evidence='evidence/actual_check.txt').status_code, 302)
        app = create_app(self.config)
        response = app.test_client().get('/')
        self.assertIn(b'Done', response.data)
        self.assertEqual(self.rows('tasks')[0]['status'], 'Done')
        with (self.source / 'events.csv').open(encoding='utf-8-sig', newline='') as file:
            exported = list(csv.DictReader(file))
        self.assertEqual(exported[-1]['actor'], 'Isolated test operator')
        self.assertEqual(exported[-1]['new_evidence'], 'evidence/actual_check.txt')
        self.assertEqual(len(exported), 2)

    def test_legacy_rows_not_rewritten_or_fabricated_on_restart(self):
        original = self.rows('events')[0]
        create_app(self.config)
        self.assertEqual(self.rows('events'), [original])
        self.assertEqual([str(original[key]) for key in EVENT_FIELDS[:7]], self.legacy)
        self.assertEqual(original['actor_kind'], 'imported-unverified')

    def test_existing_schema_migration_preserves_all_historical_columns(self):
        legacy_db = self.source / 'legacy.sqlite'
        with closing(sqlite3.connect(legacy_db)) as db, db:
            db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,title TEXT,story TEXT,criteria TEXT,requirement TEXT,owner TEXT,status TEXT,evidence TEXT)')
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)', self.task)
            db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,at TEXT,task_id TEXT,actor TEXT,old_status TEXT,new_status TEXT,note TEXT)')
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)', self.legacy)
        create_app({**self.config, 'DATABASE': str(legacy_db)})
        with closing(sqlite3.connect(legacy_db)) as db:
            row = db.execute('SELECT * FROM events').fetchone()
            self.assertEqual([str(value) for value in row[:7]], self.legacy)
            self.assertEqual(row[7], 'legacy-unverified')

    def test_invalid_fields_missing_operator_and_noop_rejected(self):
        before = (self.rows('tasks'), self.rows('events'))
        for values in [{'operator': ''}, {'owner': 'Unknown'}, {'status': 'Accepted'}, {'note': ''},
                       {'note': 'x' * 1001}, {'evidence': 'x' * 2001}, {'operator': 'x' * 81},
                       {'status': 'Doing', 'owner': '梁邦羽'}]:
            with self.subTest(values=values):
                self.assertEqual(self.post(**values).status_code, 400)
                self.assertEqual((self.rows('tasks'), self.rows('events')), before)

    def test_local_only_csp_no_javascript_and_english_ui(self):
        response = self.client.get('/')
        text = response.get_data(as_text=True)
        self.assertIn('<html lang="en">', text)
        self.assertNotIn('<script', text.lower())
        self.assertIn("script-src 'none'", response.headers['Content-Security-Policy'])
        self.assertIn('Self-reported', text)
        self.assertEqual(self.client.get('/', environ_overrides={'REMOTE_ADDR': '192.0.2.8'}).status_code, 403)

    def test_export_error_reports_committed_sqlite_state(self):
        with self.assertLogs(self.app.logger, level='ERROR'), patch('board.export_snapshot', side_effect=PermissionError('synthetic permission failure')):
            response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows('tasks')[0]['status'], 'Review / Test')
        self.assertIn(b'CSV snapshots could not be refreshed', self.client.get('/').data)

    def test_stale_done_evidence_flagged_without_state_rewrite(self):
        self.assertEqual(self.post(status='Done', evidence='evidence/actual_check.txt').status_code, 302)
        (self.delivery / 'evidence' / 'actual_check.txt').unlink()
        self.assertIn(b'Existing Done state retained', self.client.get('/').data)
        self.assertEqual(self.rows('tasks')[0]['status'], 'Done')


if __name__ == '__main__':
    protected = [ROOT / name for name in ('board.sqlite', 'tasks.csv', 'events.csv', '.secret')]
    before = {path.name: file_hash(path) for path in protected}
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BoardTests))
    after = {path.name: file_hash(path) for path in protected}
    report = {'timestamp': datetime.now().astimezone().isoformat(), 'suite': 'Isolated Flask/SQLite Scrum regression',
              'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
              'successful': result.wasSuccessful(), 'real_board_files_unchanged': before == after,
              'protected_hashes_before': before, 'protected_hashes_after': after,
              'boundary': 'Synthetic fixtures only; no real team operation, personal contribution, past sprint history, rehearsal, peer testing, or teacher acceptance is asserted.'}
    output = ROOT.parent / 'evidence' / 'website_scrum_results.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    combined = json.loads(output.read_text(encoding='utf-8')) if output.exists() else {}
    combined.update(report)
    output.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() and before == after else 1)
