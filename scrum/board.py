"""第 2 组内部看板：普通表单、SQLite 持久化，不运行 JavaScript。"""
import csv
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from threading import Lock

from flask import Flask, abort, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

ROOT = Path(__file__).resolve().parent
STATUSES = ['Backlog', 'To do', 'Doing', 'Review / Test', 'Done']
MEMBERS = ['梁邦羽', '李远向', '柳懿轩', '刘帅', '全组']
TASK_FIELDS = ['id', 'title', 'story', 'criteria', 'requirement', 'owner', 'status', 'evidence']
EVENT_FIELDS = ['id', 'at', 'task_id', 'actor', 'old_status', 'new_status', 'note',
                'actor_kind', 'old_owner', 'new_owner', 'old_evidence', 'new_evidence']
SNAPSHOT_LOCK = Lock()


def timestamp():
    """操作时间只取服务器当前时间，不提供回填历史的表单。"""
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def database():
    connection = sqlite3.connect(current_app.config['DATABASE'], timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def evidence_files(value):
    """只接受交付目录内部实际存在的文件；拒绝目录、越界和任意说明文字。"""
    if not value.strip():
        return [], ['Provide at least one existing evidence file before marking Done.']
    delivery = Path(current_app.config['DELIVERY_ROOT']).resolve()
    paths, problems = [], []
    references = value.replace('\r\n', '\n').replace('\n', ';').split(';')
    for reference in references:
        reference = reference.strip()
        if not reference:
            continue
        supplied = Path(reference)
        if supplied.is_absolute() or PureWindowsPath(reference).is_absolute() or PureWindowsPath(reference).drive:
            problems.append(f'Use a delivery-relative path: {reference}')
            continue
        try:
            resolved = (delivery / supplied).resolve(strict=True)
            if not resolved.is_relative_to(delivery) or not resolved.is_file():
                raise ValueError('outside delivery or not a file')
            paths.append(resolved.relative_to(delivery).as_posix())
        except (OSError, ValueError, RuntimeError):
            problems.append(f'Evidence must be an existing file inside this delivery: {reference}')
    if not paths and not problems:
        problems.append('Provide at least one existing evidence file before marking Done.')
    return paths, problems


def initialize():
    """原数据库只做兼容性加列；不重写已有任务、时间、操作者或历史。"""
    db_path = Path(current_app.config['DATABASE'])
    if str(db_path) != ':memory:':
        db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(database()) as db, db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,title TEXT NOT NULL,story TEXT NOT NULL,
            criteria TEXT NOT NULL,requirement TEXT NOT NULL,owner TEXT NOT NULL,status TEXT NOT NULL,
            evidence TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at TEXT NOT NULL,task_id TEXT NOT NULL,
            actor TEXT NOT NULL,old_status TEXT NOT NULL,new_status TEXT NOT NULL,note TEXT NOT NULL);
        ''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(events)')}
        for column in EVENT_FIELDS[7:]:
            if column not in columns:
                default = 'legacy-unverified' if column == 'actor_kind' else ''
                db.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
        if not db.execute('SELECT 1 FROM tasks').fetchone():
            source = Path(current_app.config['IMPORT_DIR']) / 'tasks.csv'
            if source.is_file():
                with source.open(encoding='utf-8-sig', newline='') as file:
                    for task in csv.DictReader(file):
                        if task['status'] not in STATUSES or task['owner'] not in MEMBERS:
                            raise ValueError('The initial task snapshot contains an unsupported status or proposed owner.')
                        db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)', [task.get(key, '') for key in TASK_FIELDS])
            # 仅导入已有记录，不据任务状态推测过去发生过哪些操作。
            source = Path(current_app.config['IMPORT_DIR']) / 'events.csv'
            if source.is_file() and not db.execute('SELECT 1 FROM events').fetchone():
                with source.open(encoding='utf-8-sig', newline='') as file:
                    for event in csv.DictReader(file):
                        values = [event.get(key, 'imported-unverified' if key == 'actor_kind' else '') for key in EVENT_FIELDS]
                        db.execute(f"INSERT INTO events({','.join(EVENT_FIELDS)}) VALUES ({','.join('?' for _ in EVENT_FIELDS)})", values)


def export_snapshot():
    """SQLite 为权威来源；CSV 使用临时文件替换，避免留下半截快照。"""
    destination = Path(current_app.config['EXPORT_DIR'])
    destination.mkdir(parents=True, exist_ok=True)
    with closing(database()) as db:
        db.execute('BEGIN')
        snapshots = {table: db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall() for table in ('tasks', 'events')}
    for table, rows in snapshots.items():
        temporary = destination / f'.{table}.{secrets.token_hex(6)}.tmp'
        try:
            with temporary.open('x', encoding='utf-8-sig', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(TASK_FIELDS if table == 'tasks' else EVENT_FIELDS)
                writer.writerows(tuple(row) for row in rows)
            os.replace(temporary, destination / f'{table}.csv')
        finally:
            temporary.unlink(missing_ok=True)


def create_app(config=None):
    """测试可注入临时数据库、快照目录和密钥，不接触真实看板文件。"""
    app = Flask(__name__)
    db_setting = Path(os.environ.get('SCRUM_DB_PATH', ROOT / 'board.sqlite'))
    app.config.update(DATABASE=str(db_setting if db_setting.is_absolute() else ROOT / db_setting),
                      DELIVERY_ROOT=ROOT.parent, IMPORT_DIR=ROOT, EXPORT_DIR=ROOT,
                      SESSION_COOKIE_NAME='scrum_session', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', MAX_CONTENT_LENGTH=16 * 1024)
    if config:
        app.config.update(config)
    if not app.config.get('SECRET_KEY'):
        app.config['SECRET_KEY'] = os.environ.get('SCRUM_SECRET_KEY')
    if not app.config.get('SECRET_KEY'):
        secret = ROOT / '.secret'
        try:
            with secret.open('x', encoding='ascii') as file:
                file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        app.config['SECRET_KEY'] = secret.read_text(encoding='ascii').strip()

    @app.before_request
    def protect_forms():
        if request.remote_addr not in ('127.0.0.1', '::1'):
            abort(403, 'This internal board is available only on this computer.')
        csrf = session.get('csrf')
        if not isinstance(csrf, str) or not 32 <= len(csrf) <= 128 or not csrf.isascii():
            session['csrf'] = secrets.token_urlsafe(32)
        if request.method == 'POST':
            supplied = request.form.get('csrf_token', '')
            if not supplied.isascii() or not secrets.compare_digest(session['csrf'], supplied):
                abort(400, 'The form security token is missing or invalid. Reload the board and try again.')

    @app.after_request
    def headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(HTTPException)
    def request_error(error):
        return render_template('error.html', error=error), error.code

    @app.get('/')
    def board():
        with closing(database()) as db:
            tasks = [dict(row) for row in db.execute('SELECT * FROM tasks ORDER BY id')]
            events = db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 50').fetchall()
        for task in tasks:
            _, problems = evidence_files(task['evidence'])
            task['evidence_warning'] = task['status'] == 'Done' and bool(problems)
        return render_template('board.html', tasks=tasks, events=events, statuses=STATUSES,
                               members=MEMBERS, csrf=session['csrf'])

    @app.post('/tasks/<task_id>')
    def update(task_id):
        status, owner = request.form.get('status'), request.form.get('owner')
        note = request.form.get('note', '').strip()
        operator = request.form.get('operator', '').strip()
        evidence = request.form.get('evidence', '').strip()
        if status not in STATUSES or owner not in MEMBERS:
            abort(400, 'Select a supported status and a proposed owner from the list.')
        if not note or len(note) > 1000 or not operator or len(operator) > 80 or len(evidence) > 2000:
            abort(400, 'Provide the actual local operator (1–80 characters), a change note (1–1000 characters), and evidence references no longer than 2000 characters.')
        if status == 'Done':
            paths, problems = evidence_files(evidence)
            if problems:
                abort(400, ' '.join(problems))
            evidence = '; '.join(paths)
        with closing(database()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not task:
                abort(404, 'The selected task does not exist.')
            if (task['status'], task['owner'], task['evidence']) == (status, owner, evidence):
                abort(400, 'No task fields changed. Update the status, proposed owner, or evidence before saving.')
            db.execute('UPDATE tasks SET status=?,owner=?,evidence=? WHERE id=?', (status, owner, evidence, task_id))
            db.execute('''INSERT INTO events(at,task_id,actor,old_status,new_status,note,actor_kind,
                          old_owner,new_owner,old_evidence,new_evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                       (timestamp(), task_id, operator, task['status'], status, note, 'self-reported-local',
                        task['owner'], owner, task['evidence'], evidence))
        try:
            # 单进程本地 Flask 的快照串行化；不承诺多进程部署。
            with SNAPSHOT_LOCK:
                export_snapshot()
            flash('Change saved. The operation log and CSV snapshots were updated.', 'success')
        except OSError:
            app.logger.exception('看板变更已写入 SQLite，但 CSV 快照导出失败。')
            flash('Change saved in SQLite. CSV snapshots could not be refreshed; check folder permissions before retrying.', 'warning')
        return redirect(url_for('board'))

    with app.app_context():
        initialize()
    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5001, debug=False)
