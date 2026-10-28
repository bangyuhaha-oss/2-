r"""独立的离线安装和真实进程重启验证，不计入 TC01–TC40。

运行：.venv\Scripts\python.exe tests\website_offline_check.py
使用全新临时虚拟环境、数据库和动态端口；不依赖 Playwright，不访问 5000 主服务。
"""
import hashlib
import html
import http.cookiejar
import json
import os
import platform
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from contextlib import ExitStack, closing
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT.parent / 'evidence'
CHINA_TIME = timezone(timedelta(hours=8))

# 只在测试子进程中启用标准输入停止命令，网站没有额外的关闭路由。
SERVER_CODE = r'''
import json, os, threading
from app import create_app
from werkzeug.serving import make_server
application = create_app({'DATABASE': os.environ['WEBSITE_CHECK_DATABASE'],
                          'SECRET_KEY': os.environ['WEBSITE_CHECK_SECRET'],
                          'ONSITE_NETWORKS': '127.0.0.1/32',
                          'SESSION_COOKIE_SECURE': False, 'TESTING': False})
server = make_server('127.0.0.1', 0, application)
def stop_from_test_pipe():
    import sys
    if sys.stdin.readline().strip() == 'STOP':
        server.shutdown()
threading.Thread(target=stop_from_test_pipe, daemon=True).start()
print(json.dumps({'status': 'ready', 'port': server.server_port, 'pid': os.getpid()}), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
print('Controlled WSGI shutdown completed.', flush=True)
'''


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


class FormFields(HTMLParser):
    """读取普通 HTML 表单，不执行网页脚本。"""
    def __init__(self, text):
        super().__init__()
        self.inputs = []
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        if tag == 'input':
            self.inputs.append(dict(attributes))

    def hidden(self, name):
        for field in self.inputs:
            if field.get('type') == 'hidden' and field.get('name') == name:
                return html.unescape(field.get('value', ''))
        raise AssertionError('Missing hidden field: ' + name)


class BrowserForms:
    """标准库 HTTP、Cookie 和表单客户端；不是浏览器视觉验收。"""
    def __init__(self, base):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, path, form=None):
        data = urllib.parse.urlencode(form, doseq=True).encode('utf-8') if form is not None else None
        request = urllib.request.Request(self.base + path, data=data)
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return {'status': response.code, 'url': response.url,
                    'text': response.read().decode('utf-8'), 'headers': dict(response.headers)}

    def login(self, username):
        page = self.request('/login')
        require(page['status'] == 200, 'Login form did not load.')
        response = self.request('/login', {'csrf_token': FormFields(page['text']).hidden('csrf_token'),
                                           'username': username, 'password': 'Demo!2026'})
        require(response['status'] == 200 and '/login' not in response['url'], 'Role login failed: ' + username)
        require("script-src 'none'" in response['headers'].get('Content-Security-Policy', ''), 'Runtime script policy changed.')
        return response


class ProcessServer:
    def __init__(self, python, database, secret, log, runs):
        self.python, self.database, self.secret, self.log, self.runs = python, database, secret, log, runs
        self.process = self.reader = None
        self.lines = []

    def start(self):
        environment = os.environ.copy()
        environment.update(PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8',
                           WEBSITE_CHECK_DATABASE=str(self.database), WEBSITE_CHECK_SECRET=self.secret)
        self.process = subprocess.Popen([str(self.python), '-c', SERVER_CODE], cwd=ROOT,
                                        env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        messages = queue.Queue()
        self.log.write('\n=== Process start ===\n')
        self.log.flush()

        def read_output():
            for line in self.process.stdout:
                self.lines.append(line.rstrip())
                self.log.write(line)
                self.log.flush()
                messages.put(line)

        self.reader = threading.Thread(target=read_output, daemon=True)
        self.reader.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=min(1, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                require(self.process.poll() is None, 'WSGI process exited during startup: ' + '\n'.join(self.lines[-8:]))
                continue
            try:
                readiness = json.loads(line)
            except json.JSONDecodeError:
                continue
            if readiness.get('status') == 'ready':
                require(readiness['port'] != 5000, 'The integration check must not use the main application port.')
                self.runs.append({'pid': readiness['pid'], 'port': readiness['port'], 'startup': 'PASS'})
                return 'http://127.0.0.1:' + str(readiness['port'])
        raise AssertionError('WSGI startup timed out.')

    def stop(self):
        if self.process is None:
            return
        method = 'controlled stdin STOP / WSGI shutdown'
        if self.process.poll() is None:
            try:
                self.process.stdin.write('STOP\n')
                self.process.stdin.flush()
                self.process.stdin.close()
                self.process.wait(timeout=15)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                method = 'terminate fallback'
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    method = 'kill fallback'
                    self.process.kill()
                    self.process.wait(timeout=5)
        if self.reader:
            self.reader.join(timeout=5)
        if self.runs:
            self.runs[-1].update(stop_method=method, exit_code=self.process.returncode)
        self.process.stdout.close()
        code = self.process.returncode
        self.process = None
        require(method == 'controlled stdin STOP / WSGI shutdown' and code == 0,
                'The isolated WSGI process did not complete a controlled normal stop.')


def snapshot(database, employee_id):
    """只读快照逐行比较业务结果，不把会话刷新误算为业务数据改变。"""
    require(database.is_file(), 'The isolated database is missing.')
    with closing(sqlite3.connect(str(database))) as db:
        db.row_factory = sqlite3.Row
        return {
            'employees_total': db.execute('SELECT COUNT(*) FROM employees').fetchone()[0],
            'employee': dict(db.execute('SELECT * FROM employees WHERE id=?', (employee_id,)).fetchone()),
            'schedule_weeks': [dict(row) for row in db.execute('SELECT * FROM schedule_weeks ORDER BY id')],
            'assignments': [dict(row) for row in db.execute('SELECT * FROM assignments ORDER BY schedule_id,employee_id,day,shift')],
            'checkins': [dict(row) for row in db.execute('SELECT * FROM checkins ORDER BY id')],
        }


def snapshot_summary(value):
    return {'employees_total': value['employees_total'], 'employee': value['employee'],
            'schedule_weeks': len(value['schedule_weeks']), 'assignments': len(value['assignments']),
            'checkins': len(value['checkins']), 'sha256': digest(value)}


def main():
    EVIDENCE.mkdir(exist_ok=True)
    started = time.monotonic()
    evidence = {'release': '2.0.0-flask', 'recorded_at': datetime.now(CHINA_TIME).isoformat(timespec='seconds'),
                'environment': {'python': platform.python_version(), 'system': platform.platform()},
                'evidence_type': 'Fresh temporary virtual environment, wheelhouse install, real isolated WSGI subprocesses and ordinary HTTP forms',
                'runtime_database_used': False, 'main_port_used': False, 'process_runs': [], 'checks': [],
                'not_established': ['a clean operating system', 'real workplace or external WAN acceptance',
                                    'teacher or independent representative-user acceptance', 'browser visual acceptance', '500 concurrent users']}
    server = None
    try:
        requirements = ROOT / 'requirements.txt'
        expected = dict(line.strip().split('==', 1) for line in requirements.read_text(encoding='utf-8-sig').splitlines()
                        if line.strip() and not line.lstrip().startswith('#'))
        require(len(expected) == 7, 'The delivered runtime lock must identify seven packages.')
        setup = ROOT / 'Setup.ps1'
        setup_bytes = setup.read_bytes()
        require(setup_bytes.startswith(b'\xef\xbb\xbf'), 'Setup.ps1 must retain its UTF-8 BOM for Windows PowerShell 5.1.')
        with tempfile.TemporaryDirectory(prefix='meiti-website-offline-') as directory, \
                (EVIDENCE / 'website_offline_process.log').open('w', encoding='utf-8') as process_log, ExitStack() as shutdown_guard:
            temporary = Path(directory).resolve()
            require(temporary.parent == Path(tempfile.gettempdir()).resolve(), 'Unexpected temporary workspace.')
            environment = temporary / 'fresh-runtime'
            database = temporary / 'process.sqlite'
            require(database != (ROOT / 'instance' / 'workforce.sqlite').resolve(), 'Refusing the installed runtime database.')
            require(not environment.exists(), 'The installation environment must be new.')
            base_python = Path(sys._base_executable).resolve()
            install_command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(setup),
                               '-InstallOnly', '-EnvironmentPath', str(environment), '-PythonPath', str(base_python)]
            install_environment = os.environ.copy()
            install_environment.update(PIP_NO_INDEX='1', PIP_NO_CACHE_DIR='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
                                       PIP_CONFIG_FILE=os.devnull, PYTHONIOENCODING='utf-8')
            installed = subprocess.run(install_command, cwd=ROOT, env=install_environment,
                                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
            installation_log = 'Command arguments: ' + json.dumps(install_command, ensure_ascii=False) + '\n\n' + installed.stdout + installed.stderr
            (EVIDENCE / 'website_offline_install.log').write_text(installation_log, encoding='utf-8')
            evidence['installation'] = {'command': install_command, 'exit_code': installed.returncode,
                                        'new_virtual_environment': True, 'base_python': str(base_python),
                                        'setup_sha256': hashlib.sha256(setup_bytes).hexdigest(),
                                        'requirements_sha256': hashlib.sha256(requirements.read_bytes()).hexdigest(),
                                        'local_install_policy': ['--no-index', '--no-cache-dir', '--only-binary=:all:', 'local wheelhouse'],
                                        'log': 'evidence/website_offline_install.log'}
            require(installed.returncode == 0, 'Offline setup failed; inspect website_offline_install.log.')
            python = environment / 'Scripts' / 'python.exe'
            probe_code = "import importlib.metadata as m,importlib.util,json,platform,sys; print(json.dumps({'python':platform.python_version(),'executable':sys.executable,'distributions':{d.metadata['Name']:d.version for d in m.distributions()},'playwright_importable':importlib.util.find_spec('playwright') is not None}))"
            probe = subprocess.run([str(python), '-c', probe_code], cwd=ROOT, capture_output=True,
                                   text=True, encoding='utf-8', timeout=30, check=True)
            packages = json.loads(probe.stdout)
            normalized = {name.lower(): version for name, version in packages['distributions'].items()}
            require(all(normalized.get(name.lower()) == version for name, version in expected.items()), 'Installed runtime versions differ from requirements.txt.')
            require(not packages['playwright_importable'] and not any('playwright' in name.lower() for name in normalized), 'The fresh runtime must not include Playwright.')
            require(Path(packages['executable']).resolve() == python.resolve(), 'Runtime probe did not use the new environment.')
            evidence['installation'].update(runtime=packages, locked_runtime_versions=expected, no_playwright=True)
            evidence['checks'].append({'name': 'Fresh offline installation and exact runtime packages', 'status': 'PASS'})
            print('Fresh offline installation passed; starting isolated HTTP workflows.', flush=True)

            server = ProcessServer(python, database, os.urandom(32).hex(), process_log, evidence['process_runs'])
            shutdown_guard.callback(server.stop)
            base = server.start()
            hr, supervisor, employee = (BrowserForms(base) for _ in range(3))
            hr.login('hr')
            new_page = hr.request('/employees/new')
            created_values = {'name': 'Offline Verification Employee', 'age': '29', 'position': 'Verification role',
                              'department': 'Verification group', 'address': 'Fictional verification address',
                              'pay_info': 'Fictional agreed pay text', 'emergency_contact': 'Fictional verification contact'}
            created = hr.request('/employees/new', {'csrf_token': FormFields(new_page['text']).hidden('csrf_token'), **created_values})
            match = re.search(r'/employees/(\d+)$', created['url'])
            require(created['status'] == 200 and match, 'HR creation did not return the saved employee record.')
            employee_id = int(match.group(1))
            require(all(value in created['text'] for value in created_values.values()), 'Created seven-field record did not round-trip through HTTP.')
            edited_page = hr.request(f'/employees/{employee_id}/edit')
            fields = FormFields(edited_page['text'])
            updated_values = {'name': 'Offline Updated Employee', 'age': '0', 'position': 'Updated verification role',
                              'department': 'Updated verification group', 'address': 'Updated fictional address',
                              'pay_info': 'Updated free text pay agreement', 'emergency_contact': 'Updated fictional contact'}
            updated = hr.request(f'/employees/{employee_id}/edit', {'csrf_token': fields.hidden('csrf_token'),
                                                                   'version': fields.hidden('version'), **updated_values})
            require(updated['status'] == 200 and all(value in updated['text'] for value in updated_values.values()), 'HR update failed.')
            searched = hr.request('/employees?name=' + urllib.parse.quote(updated_values['name']))
            require(searched['status'] == 200 and updated_values['name'] in searched['text'], 'Saved employee search failed.')
            evidence['checks'].append({'name': 'HR HTTP create, read, seven-field update and search', 'status': 'PASS'})

            supervisor.login('supervisor')
            schedule_page = supervisor.request('/team-schedule')
            fields = FormFields(schedule_page['text'])
            week = fields.hidden('week')
            member_ids = sorted({int(match.group(1)) for field in fields.inputs
                                 if (match := re.fullmatch(r'shift_(\d+)_\d{4}-\d{2}-\d{2}', field.get('name', '')))})
            require(member_ids == [1, 3, 4], 'The supervisor HTTP page did not contain the expected own-team fixture.')
            form = [('csrf_token', fields.hidden('csrf_token')), ('week', week), ('version', fields.hidden('version'))]
            # 测试中手工指定的固定示例，不为运行网站增加自动排班能力。
            for member_id in member_ids:
                for offset in range(5):
                    day = (date.fromisoformat(week) + timedelta(days=offset)).isoformat()
                    chosen = ('Afternoon', 'Evening') if member_id == 1 and offset == 0 else ('Morning', 'Afternoon')
                    form.extend((f'shift_{member_id}_{day}', shift) for shift in chosen)
            saved_schedule = supervisor.request('/team-schedule', form)
            require(saved_schedule['status'] == 200 and 'The complete team week was saved.' in saved_schedule['text'], 'The complete team revision failed.')
            evidence['checks'].append({'name': 'Supervisor HTTP valid manual team-week revision', 'status': 'PASS'})

            employee.login('employee')
            personal = employee.request('/my-schedule?week=' + week)
            require(personal['status'] == 200 and 'Evening' in personal['text'], 'Employee did not see the revised own schedule.')
            checkin_page = employee.request('/check-in')
            fields = FormFields(checkin_page['text'])
            operation_id = fields.hidden('operation_id')
            recorded = employee.request('/check-in', {'csrf_token': fields.hidden('csrf_token'), 'operation_id': operation_id})
            require(recorded['status'] == 200 and 'Check-in saved at ' in recorded['text'], 'Employee HTTP check-in failed.')
            before = snapshot(database, employee_id)
            require(before['employees_total'] == 501 and len(before['checkins']) == 1, 'Unexpected saved record counts before restart.')
            require(all(str(before['employee'][field]) == value for field, value in updated_values.items()),
                    'The saved database record does not match all seven updated form fields.')
            evidence['checks'].append({'name': 'Employee own revised schedule and source-IP check-in over HTTP', 'status': 'PASS',
                                        'condition': 'Synthetic loopback source; not real store presence'})
            evidence['before_restart'] = snapshot_summary(before)
            server.stop()
            print('HTTP workflows passed; first WSGI process stopped normally. Restarting.', flush=True)

            base = server.start()
            for client in (hr, supervisor, employee):
                client.base = base
            after = snapshot(database, employee_id)
            require(after == before, 'Business rows changed across the real process restart.')
            evidence['after_restart'] = snapshot_summary(after)
            evidence['restart_row_comparison'] = {'all_employee_fields_equal': after['employee'] == before['employee'],
                                                   'all_schedule_versions_equal': after['schedule_weeks'] == before['schedule_weeks'],
                                                   'all_assignment_rows_equal': after['assignments'] == before['assignments'],
                                                   'all_checkin_rows_equal': after['checkins'] == before['checkins']}
            persisted = hr.request(f'/employees/{employee_id}')
            require(persisted['status'] == 200 and all(value in persisted['text'] for value in updated_values.values()), 'Updated employee was not readable after process restart.')
            require('Evening' in employee.request('/my-schedule?week=' + week)['text'], 'Revised personal schedule was not readable after restart.')
            require(before['checkins'][0]['recorded_at'] in employee.request('/check-in')['text'], 'Recorded attendance was not readable after restart.')
            retry_page = employee.request('/check-in')
            retry = employee.request('/check-in', {'csrf_token': FormFields(retry_page['text']).hidden('csrf_token'), 'operation_id': operation_id})
            require(retry['status'] == 200 and snapshot(database, employee_id)['checkins'] == before['checkins'], 'Check-in retry duplicated a record after restart.')
            evidence['checks'].append({'name': 'Actual normal process stop/restart, row equality, HTTP retrieval and idempotent retry', 'status': 'PASS'})

            deletion_page = hr.request(f'/employees/{employee_id}/delete')
            fields = FormFields(deletion_page['text'])
            deleted = hr.request(f'/employees/{employee_id}/delete', {'csrf_token': fields.hidden('csrf_token'),
                                                                    'version': fields.hidden('version'), 'confirmation': updated_values['name']})
            require(deleted['status'] == 200 and 'Employee deleted.' in deleted['text'], 'Confirmed HTTP employee deletion failed.')
            require(hr.request(f'/employees/{employee_id}')['status'] == 404, 'Deleted employee remained addressable.')
            with closing(sqlite3.connect(str(database))) as db:
                final_total = db.execute('SELECT COUNT(*) FROM employees').fetchone()[0]
                orphan_rows = db.execute('PRAGMA foreign_key_check').fetchall()
            require(final_total == 500 and not orphan_rows, 'Final employee count or foreign keys were inconsistent.')
            evidence['final_current_employee_count'] = final_total
            evidence['checks'].append({'name': 'Confirmed physical deletion restores 500 records with no foreign-key orphans', 'status': 'PASS'})
            server.stop()
            require(all(run['exit_code'] == 0 for run in evidence['process_runs']), 'A WSGI child did not exit normally.')
            server = None
        require(not temporary.exists(), 'The isolated temporary workspace was not removed.')
        evidence['temporary_workspace_cleaned'] = True
        evidence['successful'] = True
    except Exception as error:
        evidence['successful'] = False
        evidence['error'] = type(error).__name__ + ': ' + str(error)
        traceback.print_exc()
    finally:
        if server is not None:
            try:
                server.stop()
            except Exception as error:
                evidence['shutdown_error'] = str(error)
                evidence['successful'] = False
        evidence['elapsed_seconds'] = round(time.monotonic() - started, 3)
        evidence['logs'] = ['evidence/website_offline_install.log', 'evidence/website_offline_process.log']
        (EVIDENCE / 'website_offline_install.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Offline runtime integration: ' + ('PASS' if evidence.get('successful') else 'FAIL'), flush=True)
    return 0 if evidence.get('successful') else 1


if __name__ == '__main__':
    sys.exit(main())
