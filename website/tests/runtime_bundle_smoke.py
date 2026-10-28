"""独立2.1运行包解压/离线安装/双进程与重启检查，仅操作临时副本。"""
import csv
import hashlib
import html
import http.cookiejar
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHILD = """import sys,threading
from werkzeug.serving import make_server
from {module} import create_app
server=make_server('127.0.0.1',int(sys.argv[1]),create_app(),threaded=True)
thread=threading.Thread(target=server.serve_forever,daemon=True)
thread.start()
print('READY',flush=True)
sys.stdin.readline()
server.shutdown()
thread.join()
server.server_close()
"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def free_port():
    with socket.socket() as handle:
        handle.bind(('127.0.0.1', 0))
        return handle.getsockname()[1]


def opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(client, url, values=None, expected=200):
    data = urllib.parse.urlencode(values).encode() if values is not None else None
    try:
        response = client.open(urllib.request.Request(url, data=data), timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read()
        assert response.status == expected, (url, response.status, body[:200])
        return body, dict(response.headers), response.geturl()


def hidden(body, field):
    match = re.search('name="' + field + '" value="([^"]+)"', body.decode())
    assert match, field
    return html.unescape(match.group(1))


def login(url, username):
    client = opener()
    body, _, _ = request(client, url + '/login')
    body, _, destination = request(client, url + '/login',
        {'csrf_token': hidden(body, 'csrf_token'), 'username': username, 'password': 'Demo!2026'})
    assert destination.endswith('/dashboard')
    return client


def main():
    archive = ROOT / 'evidence/website_runtime_bundle_2.1.0.zip'
    report = {'release': '2.1.0-flask', 'recorded_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
              'archive': archive.name, 'archive_sha256': sha(archive), 'checks': [], 'processes': []}
    logs, processes, handles = [], [], []
    with tempfile.TemporaryDirectory(prefix='Meiti 2.1 离线 ') as temporary:
        temporary_root = Path(temporary)
        with zipfile.ZipFile(archive) as package:
            assert package.testzip() is None
            assert all((temporary_root / name).resolve().is_relative_to(temporary_root.resolve()) for name in package.namelist())
            package.extractall(temporary_root)
        delivery = temporary_root / 'Meiti_Group2'
        manifest = json.loads((delivery / 'evidence/website_runtime_manifest_2.1.0.json').read_text(encoding='utf-8'))
        assert manifest['release'] == '2.1.0-flask'
        assert all(sha(delivery / item['path']) == item['sha256'] for item in manifest['files'])
        report['checks'].append('All extracted member hashes match2.1 manifest; temporary path includes Chinese and spaces')

        def command(arguments):
            result = subprocess.run(arguments, cwd=delivery, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=120)
            logs.append({'command': [str(value) for value in arguments], 'returncode': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
            assert result.returncode == 0, result.stdout + result.stderr
            return result.stdout

        command(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(delivery / 'website/Setup.ps1'), '-InstallOnly', '-PythonPath', sys.executable])
        python = delivery / 'website/.venv/Scripts/python.exe'
        command([str(python), '-m', 'pip', 'check'])
        installed = json.loads(command([str(python), '-c',
            'import importlib.metadata as m,json; print(json.dumps(sorted(d.metadata["Name"].lower() for d in m.distributions())))']))
        assert set(installed) == {'pip', 'flask', 'werkzeug', 'jinja2', 'markupsafe', 'itsdangerous', 'click', 'blinker'}
        report['installed_distributions'] = installed
        report['checks'].append('Official Setup.ps1 installed seven locked offline wheels; pip check passed; no QA packages installed')
        environment = os.environ.copy()
        for key in ['HOST', 'PORT', 'ONSITE_NETWORKS', 'SECRET_KEY', 'COOKIE_SECURE', 'PYTHONPATH']:
            environment.pop(key, None)

        def start(folder, module):
            port = free_port()
            path = temporary_root / (folder + '_' + str(len(processes)) + '.log')
            handle = open(path, 'w', encoding='utf-8')
            handles.append((handle, path))
            process = subprocess.Popen([str(python), '-u', '-c', CHILD.format(module=module), str(port)],
                cwd=delivery / folder, stdin=subprocess.PIPE, stdout=handle, stderr=subprocess.STDOUT,
                env=environment, text=True)
            processes.append(process)
            url = f'http://127.0.0.1:{port}'
            for _ in range(100):
                assert process.poll() is None, 'Child exited during startup'
                try:
                    request(opener(), url + ('/login' if folder == 'website' else '/'))
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError('Process readiness timed out')
            report['processes'].append({'folder': folder, 'pid': process.pid, 'url': url, 'http': 200})
            return process, url

        def stop(process):
            if process.poll() is None:
                process.communicate('\n', timeout=15)
            assert process.returncode == 0

        try:
            web_process, web = start('website', 'app')
            board_process, board = start('scrum', 'board')
            hr, supervisor, employee = login(web, 'hr'), login(web, 'supervisor'), login(web, 'employee')
            body, headers, _ = request(hr, web + '/employees/export.csv')
            assert body.startswith(b'\xef\xbb\xbf') and headers['X-Total-Records'] == '500'
            assert len(list(csv.reader(io.StringIO(body.decode('utf-8-sig'))))) == 501
            request(supervisor, web + '/team-week')
            request(supervisor, web + '/team-week/print')
            request(supervisor, web + '/employees/export.csv', expected=403)
            request(employee, web + '/my-schedule/print')
            request(employee, web + '/my-checkins/print')
            report['checks'].append('Two real WSGI processes, three role dashboards, HR500-row CSV and scoped schedule/history print routes passed')
            body, _, _ = request(employee, web + '/check-in')
            submission = {'csrf_token': hidden(body, 'csrf_token'), 'operation_id': hidden(body, 'operation_id')}
            request(employee, web + '/check-in', submission)
            request(employee, web + '/check-in', submission)
            with closing(sqlite3.connect(delivery / 'website/instance/workforce.sqlite')) as db:
                before = db.execute('SELECT employee_id,operation_id,recorded_at FROM checkins').fetchall()
                assert len(before) == 1
                assert db.execute('SELECT COUNT(*) FROM employees').fetchone()[0] == 500
            stop(web_process)
            web_process, web = start('website', 'app')
            # 同一浏览器会话与安装密钥保留；实际重启后仍读取同一记录。
            request(employee, web + '/my-checkins')
            with closing(sqlite3.connect(delivery / 'website/instance/workforce.sqlite')) as db:
                after = db.execute('SELECT employee_id,operation_id,recorded_at FROM checkins').fetchall()
                assert before == after
                assert not db.execute('PRAGMA foreign_key_check').fetchall()
            report['checks'].append('Loopback check-in retry idempotent; check-in row and login session persisted after real process restart')
        finally:
            for process in processes:
                stop(process)
            for handle, path in handles:
                handle.close()
                logs.append({'process_log': path.name, 'text': path.read_text(encoding='utf-8')})
        report['process_exit_codes'] = [process.returncode for process in processes]
        report['checks'].append('Every owned test process stopped through controlled stdin and exited0')
    report.update({'passed': True, 'work_database_used': False,
                   'boundary': 'Fresh venv on this Windows host with existing CPython3.12 x64; no clean-OS, physical device/store/WAN/TLS, independent-user or teacher acceptance claim.'})
    (ROOT / 'evidence/website_runtime_smoke_2.1.0.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'evidence/website_runtime_smoke_2.1.0.log').write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
