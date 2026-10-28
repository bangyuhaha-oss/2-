"""第 2 组员工管理网站，Python + Flask + SQLite + Jinja，无 JavaScript。

讲解顺序：配置与请求准备 → 登录权限 → 员工管理 → 团队排班 → 个人排班/签到。
"""
import ipaddress
import os
import secrets
import sqlite3
import time
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from database import connect, initialize
from models import CheckIn, Employee, ScheduleWeek, SHIFTS, Team, User, ValidationError, zhengzhou_today
import workspace

ROOT = Path(__file__).resolve().parent
RELEASE = '2.1.0-flask'


def create_app(config=None):
    """入口：测试使用独立临时库，实际运行使用 instance/workforce.sqlite。"""
    app = Flask(__name__)
    (ROOT / 'instance').mkdir(exist_ok=True)
    secret_file = ROOT / 'instance' / 'secret.key'
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32), encoding='ascii')
    app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY') or secret_file.read_text().strip(),
                      DATABASE=str(ROOT / 'instance' / 'workforce.sqlite'),
                      ONSITE_NETWORKS=os.environ.get('ONSITE_NETWORKS', '127.0.0.1/32,::1/128'),
                      MAX_CONTENT_LENGTH=1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1')
    if config:
        app.config.update(config)
    # 配置拼写错误时拒绝启动，避免签到规则意外放宽。
    onsite_networks = [ipaddress.ip_network(s.strip())
                       for s in app.config['ONSITE_NETWORKS'].split(',') if s.strip()]
    initialize(app.config['DATABASE'])

    @app.before_request
    def prepare_request():
        g.db = connect(app.config['DATABASE'])
        token = session.get('sid', '')
        if not isinstance(token, str) or len(token) > 256:
            session.pop('sid', None)
            token = ''
        g.user = User.from_session(g.db, token, time.time())
        csrf = session.get('csrf')
        if not isinstance(csrf, str) or not 32 <= len(csrf) <= 128 or not csrf.isascii():
            session['csrf'] = secrets.token_urlsafe(32)
        # 所有 POST 都检查服务端签发的防伪标识；GET 永不改动业务数据。
        if request.method == 'POST':
            supplied = request.form.get('csrf_token', '')
            if not supplied.isascii() or not secrets.compare_digest(session['csrf'], supplied):
                abort(400, 'The form expired. Reload the page and try again.')

    @app.teardown_appcontext
    def close_database(error=None):
        db = g.pop('db', None)
        if db:
            db.close()

    @app.after_request
    def security_headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.context_processor
    def shared_values():
        user = g.get('user')
        team = g.db.execute('SELECT name FROM teams WHERE id=?', (user.team_id,)).fetchone() if user and user.role == 'supervisor' else None
        return {'csrf_token': session.get('csrf', ''), 'release': RELEASE, 'shifts': SHIFTS,
                'current_team_name': team['name'] if team else None}

    def role_required(role):
        """权限在后端统一检查，隐藏按钮不能代替权限控制。"""
        def decorate(function):
            @wraps(function)
            def protected(*args, **kwargs):
                if not g.user:
                    return redirect(url_for('login'))
                if g.user.role not in role.split(','):
                    abort(403)
                # 同时检查查询串和表单的全部同名参数，不能借重复参数切换身份。
                field, expected = ('team_id', g.user.team_id) if g.user.role == 'supervisor' else ('employee_id', g.user.employee_id)
                if g.user.role != 'hr' and any(value != str(expected) for source in (request.args, request.form)
                                               for value in source.getlist(field)):
                    abort(403)
                unrelated = 'employee_id' if g.user.role == 'supervisor' else 'team_id'
                if g.user.role != 'hr' and any(unrelated in source for source in (request.args, request.form)):
                    abort(403)
                return function(*args, **kwargs)
            return protected
        return decorate

    def employee_or_404(employee_id):
        employee = Employee.get(g.db, employee_id)
        if not employee:
            abort(404)
        return employee

    def week_dates():
        """日期统一归到所选周的周一，页面明确显示周区间。"""
        try:
            selected = date.fromisoformat(request.values.get('week', zhengzhou_today().isoformat()))
            monday = selected - timedelta(days=selected.weekday())
            if monday < date.min + timedelta(days=7) or monday > date.max - timedelta(days=7):
                raise ValueError('Week navigation is outside the supported date range.')
            return [(monday + timedelta(days=i)).isoformat() for i in range(7)]
        except (ValueError, OverflowError):
            abort(400, 'Select a valid date for the week.')

    def directory_context():
        """返回链接只保留目录白名单参数，不接受任意next跳转地址。"""
        try:
            values, where, params, context = workspace.directory_filters(request.args, g.user)
        except ValidationError as error:
            abort(400, str(error))
        return {'directory_args': context, 'directory_url': url_for('employees', **context)}

    def own_team_week(days):
        """只读取当前主管团队及当前成员；其他团队参数已在统一权限处拒绝。"""
        row = g.db.execute('SELECT * FROM teams WHERE id=?', (g.user.team_id,)).fetchone()
        if not row:
            abort(403)
        team = Team(**dict(row))
        members = team.members(g.db)
        saved, selected = ScheduleWeek(team.id, days[0]).load(g.db)
        member_ids = {member['id'] for member in members}
        selected = {key: shifts for key, shifts in selected.items() if key[0] in member_ids and key[1] in days}
        return team, members, saved, selected

    # 第一部分：登录与退出，对应 FR-01 / UC-01、UC-11。
    @app.route('/')
    def home():
        if not g.user:
            return redirect(url_for('login'))
        return redirect(url_for('dashboard'))

    @app.get('/dashboard')
    @role_required('hr,supervisor,employee')
    def dashboard():
        """三类首页只展示各自职责和实际保存记录，不制造待审批或缺勤数字。"""
        days = week_dates()
        common = {'today': workspace.zhengzhou_now().date().isoformat(), **workspace.week_links(days)}
        if g.user.role == 'hr':
            total, grouped = workspace.workforce_summary(g.db)
            recent = g.db.execute('SELECT id,name,position,department FROM employees ORDER BY id DESC LIMIT 5').fetchall()
            return render_template('dashboard.html', total=total, grouped=grouped, recent=recent,
                                   department_count=len([row for row in grouped['department'] if not row['is_empty']]),
                                   position_count=len([row for row in grouped['position'] if not row['is_empty']]), **common)
        if g.user.role == 'supervisor':
            # 工作台固定显示真实本周；用户选周在周预览/编辑中进行。
            monday = date.fromisoformat(common['current_week'])
            days = [(monday + timedelta(days=offset)).isoformat() for offset in range(7)]
            team, members, saved, selected = own_team_week(days)
            return render_template('dashboard.html', team=team, members=members, saved=saved,
                                   total=len(members), days=days, selected=selected,
                                   **workspace.team_week_summary(members, days, selected), **common)
        employee = employee_or_404(g.user.employee_id)
        return render_template('dashboard.html', employee=employee,
                               personal=workspace.personal_week(g.db, employee.id, days), **common)

    @app.get('/help')
    @role_required('hr,supervisor,employee')
    def help_page():
        """真实帮助和版本入口，无额外业务操作或外部依赖。"""
        return render_template('help.html', page_title='Help & release')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()[:120]
            source = request.remote_addr or 'unknown'
            cutoff = time.time() - 900
            failures = g.db.execute('SELECT COUNT(*) FROM login_failures WHERE source=? AND username=? AND attempted_at>?',
                                    (source, username, cutoff)).fetchone()[0]
            if failures >= 10:
                return render_template('login.html', error='Too many attempts. Try again in 15 minutes.'), 429
            user = User.authenticate(g.db, username, request.form.get('password', ''))
            if user:
                token = secrets.token_urlsafe(32)
                with g.db:
                    # 重登替换当前浏览器的旧会话，旧签名 Cookie 不能再次恢复该登录。
                    g.db.execute('DELETE FROM sessions WHERE token=?', (session.get('sid', ''),))
                    g.db.execute('DELETE FROM sessions WHERE expires_at<=?', (time.time(),))
                    g.db.execute('INSERT INTO sessions VALUES (?,?,?)', (token, user.id, time.time() + 8 * 3600))
                    g.db.execute('DELETE FROM login_failures WHERE source=? AND username=?', (source, username))
                session.clear()
                session.update(sid=token, csrf=secrets.token_urlsafe(32))
                return redirect(url_for('home'))
            with g.db:
                g.db.execute('DELETE FROM login_failures WHERE attempted_at<=?', (cutoff,))
                g.db.execute('INSERT INTO login_failures VALUES (?,?,?)', (source, username, time.time()))
            return render_template('login.html', error='Username or password is incorrect.'), 401
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        with g.db:
            g.db.execute('DELETE FROM sessions WHERE token=?', (session.get('sid', ''),))
        session.clear()
        return redirect(url_for('login'))

    # 第二部分：员工管理，对应 FR-02 至 FR-06、FR-11、FR-12。
    @app.get('/employees')
    @role_required('hr,supervisor')
    def employees():
        try:
            values, where, params, context = workspace.directory_filters(request.args, g.user)
        except ValidationError as error:
            abort(400, str(error))
        count = g.db.execute('SELECT COUNT(*) FROM employees' + where, params).fetchone()[0]
        page = workspace.page_number(context['page'], count)
        context['page'] = page
        rows = g.db.execute('SELECT id,name,position,department FROM employees' + where +
                            ' ORDER BY ' + workspace.SORT_SQL[values['sort']] + ' LIMIT 20 OFFSET ?',
                            params + [(page - 1) * 20]).fetchall()
        total, grouped = workspace.workforce_summary(g.db, g.user.team_id if g.user.role == 'supervisor' else None)
        options = workspace.directory_options(g.db) if g.user.role == 'hr' else {'department_options': [], 'position_options': []}
        return render_template('employees.html', rows=rows, total=total, count=count, page=page,
                               pages=max(1, (count + 19) // 20), grouped=grouped,
                               directory_args=context, directory_url=url_for('employees', **context),
                               sort_options=workspace.SORT_OPTIONS, **values, **options)

    @app.get('/employees/export.csv')
    @role_required('hr')
    def employees_export():
        """HR导出全部匹配行；与列表使用同一条件及排序，不受当前20行分页限制。"""
        if any(field in request.args for field in ('employee_id', 'team_id')):
            abort(403)
        try:
            values, where, params, context = workspace.directory_filters(request.args, g.user)
        except ValidationError as error:
            abort(400, str(error))
        rows = g.db.execute('SELECT id,name,age,position,department,address,pay_info,emergency_contact FROM employees' +
                            where + ' ORDER BY ' + workspace.SORT_SQL[values['sort']], params)
        return workspace.csv_download(['Record ID', 'Name', 'Age', 'Position', 'Department', 'Address',
                                       'Pay information', 'Emergency contact'], rows, 'employees-matching.csv')

    @app.route('/employees/new', methods=['GET', 'POST'])
    @role_required('hr')
    def employee_new():
        context = directory_context()
        if request.method == 'POST':
            try:
                values = Employee.validate(request.form)
                employee_id = Employee.create(g.db, values)
                flash('Employee saved. The workforce total has been updated.', 'success')
                return redirect(url_for('employee_detail', employee_id=employee_id, **context['directory_args']))
            except ValidationError as error:
                return render_template('employee_form.html', values=request.form, employee=None, error=str(error), **context), 422
        return render_template('employee_form.html', values={}, employee=None, **context)

    @app.get('/employees/<int:employee_id>')
    @role_required('hr')
    def employee_detail(employee_id):
        return render_template('employee_detail.html', employee=employee_or_404(employee_id), **directory_context())

    @app.route('/employees/<int:employee_id>/edit', methods=['GET', 'POST'])
    @role_required('hr')
    def employee_edit(employee_id):
        employee = employee_or_404(employee_id)
        context = directory_context()
        if request.method == 'POST':
            try:
                values = Employee.validate(request.form)
                employee.update(g.db, values, int(request.form.get('version', '0')))
                flash('Employee information updated.', 'success')
                return redirect(url_for('employee_detail', employee_id=employee_id, **context['directory_args']))
            except (ValidationError, ValueError) as error:
                return render_template('employee_form.html', employee=employee, values=request.form,
                                       error=str(error) if isinstance(error, ValidationError) else 'Invalid record version.', **context), 422
        return render_template('employee_form.html', employee=employee, values=vars(employee), **context)

    @app.route('/employees/<int:employee_id>/delete', methods=['GET', 'POST'])
    @role_required('hr')
    def employee_delete(employee_id):
        employee = employee_or_404(employee_id)
        context = directory_context()
        if request.method == 'POST':
            try:
                if request.form.get('confirmation') != employee.name:
                    raise ValidationError('Type the employee name exactly to confirm deletion.')
                employee.delete(g.db, int(request.form.get('version', '0')))
                flash('Employee deleted. Linked accounts, sessions, assignments and check-ins were removed.', 'success')
                return redirect(context['directory_url'])
            except (ValidationError, ValueError) as error:
                return render_template('employee_delete.html', employee=employee, error=str(error), **context), 422
        return render_template('employee_delete.html', employee=employee, **context)

    # 第三部分：主管手工排班，对应 FR-07、FR-08。
    @app.get('/team-week')
    @app.get('/team-week/print', endpoint='team_week_print')
    @role_required('supervisor')
    def team_week():
        """与编辑分开的只读周预览；打印也重新检查主管和团队身份。"""
        days = week_dates()
        team, members, saved, selected = own_team_week(days)
        return render_template('team_week.html', team=team, members=members, days=days,
                               saved=saved, selected=selected,
                               print_view=request.endpoint == 'team_week_print',
                               **workspace.team_week_summary(members, days, selected), **workspace.week_links(days))

    @app.route('/team-schedule', methods=['GET', 'POST'])
    @role_required('supervisor')
    def team_schedule():
        days = week_dates()
        team, members, saved, selected = own_team_week(days)
        schedule = ScheduleWeek(team.id, days[0])
        error, status, member_errors = None, 200, {}
        draft = request.form.get('draft_source', '') if request.method == 'POST' else request.args.get('draft', '')
        if draft not in ('', 'previous'):
            abort(400, 'Select an available manual draft source.')
        draft_loaded = draft == 'previous'
        source_week = (date.fromisoformat(days[0]) - timedelta(days=7)).isoformat()
        draft_missing, draft_new_members = False, []
        if draft_loaded:
            source, source_selected = ScheduleWeek(team.id, source_week).load(g.db)
            draft_missing = source is None
            source_ids = {key[0] for key in source_selected}
            draft_new_members = [{'id': member['id'], 'name': member['name']}
                                 for member in members if member['id'] not in source_ids]
            if request.method == 'GET':
                # 只替换页面草稿；目标周saved和版本从原目标周保留，不写数据库。
                selected = {(member['id'], day): source_selected.get(
                    (member['id'], (date.fromisoformat(day) - timedelta(days=7)).isoformat()), [])
                    for member in members for day in days}
        submitted_version = request.form.get('version', '0') if request.method == 'POST' else str(saved['version'] if saved else 0)
        if request.method == 'POST':
            try:
                if draft_loaded and request.form.get('draft_reviewed') != '1':
                    raise ValidationError('Review the loaded draft and confirm it before saving the complete week.')
                schedule.save(g.db, members, days, request.form, int(request.form.get('version', '0')))
                flash('The complete team week was saved.', 'success')
                return redirect(url_for('team_schedule', week=days[0]))
            except (ValidationError, ValueError) as problem:
                error, status = str(problem), 422
                member_errors = getattr(problem, 'member_errors', {})
                selected = {(m['id'], day): request.form.getlist(f'shift_{m["id"]}_{day}')
                            for m in members for day in days}
        return render_template('team_schedule.html', team=team, members=members, days=days,
                               saved=saved, selected=selected, error=error,
                               member_errors=member_errors, submitted_version=submitted_version,
                               draft_loaded=draft_loaded, draft_missing=draft_missing,
                               draft_new_members=draft_new_members, draft_source_week=source_week,
                               **workspace.week_links(days)), status

    # 第四部分：个人排班与现场签到，对应 FR-09、FR-10。
    @app.get('/my-schedule')
    @app.get('/my-schedule/print', endpoint='my_schedule_print')
    @role_required('employee')
    def my_schedule():
        days = week_dates()
        employee = employee_or_404(g.user.employee_id)
        return render_template('my_schedule.html', employee=employee,
                               print_view=request.endpoint == 'my_schedule_print',
                               **workspace.personal_week(g.db, employee.id, days))

    @app.get('/my-checkins')
    @app.get('/my-checkins/print', endpoint='my_checkins_print')
    @role_required('employee')
    def my_checkins():
        """本人记录按郑州日期筛选；打印包含全部匹配记录，而屏幕按20条分页。"""
        try:
            where, params, filters = workspace.checkin_filters(request.args, g.user.employee_id)
            count = g.db.execute('SELECT COUNT(*) FROM checkins' + where, params).fetchone()[0]
            page = workspace.page_number(request.args.get('page', '1'), count)
        except ValidationError as error:
            abort(400, str(error))
        printing = request.endpoint == 'my_checkins_print'
        sql = 'SELECT id,employee_id,recorded_at FROM checkins' + where + ' ORDER BY recorded_at DESC,id DESC'
        rows = g.db.execute(sql if printing else sql + ' LIMIT 20 OFFSET ?',
                            params if printing else params + [(page - 1) * 20]).fetchall()
        return render_template('checkin_history.html', employee=employee_or_404(g.user.employee_id),
                               rows=rows, count=count, page=page, pages=max(1, (count + 19) // 20),
                               date_from=filters['from'], date_to=filters['to'], history_args=filters,
                               print_view=printing)

    @app.get('/my-checkins/export.csv')
    @role_required('employee')
    def my_checkins_export():
        """导出当前员工全部匹配签到；不包含签名operation_id及其他员工。"""
        try:
            where, params, filters = workspace.checkin_filters(request.args, g.user.employee_id)
        except ValidationError as error:
            abort(400, str(error))
        rows = g.db.execute('SELECT id,employee_id,recorded_at FROM checkins' + where +
                            ' ORDER BY recorded_at DESC,id DESC', params)
        return workspace.csv_download(['Record ID', 'Employee record ID', 'Recorded at (UTC+08:00)'],
                                      rows, 'my-checkins-matching.csv')

    @app.route('/check-in', methods=['GET', 'POST'])
    @role_required('employee')
    def check_in():
        if request.values.get('employee_id', str(g.user.employee_id)) != str(g.user.employee_id):
            abort(403)
        try:
            address = ipaddress.ip_address(request.remote_addr or '')
            onsite = any(address in network for network in onsite_networks)
        except ValueError:
            onsite = False
        signer = URLSafeTimedSerializer(app.secret_key, salt='check-in')
        error, status = None, 200
        if request.method == 'POST':
            if not onsite:
                error, status = 'Check-in is available only from the configured workplace network.', 403
            else:
                try:
                    payload = signer.loads(request.form.get('operation_id', ''), max_age=900)
                    if (not isinstance(payload, dict) or type(payload.get('employee_id')) is not int
                            or not isinstance(payload.get('nonce'), str)
                            or not 16 <= len(payload['nonce']) <= 128):
                        raise ValueError('Invalid check-in operation.')
                    if payload['employee_id'] != g.user.employee_id:
                        abort(403)
                    record = CheckIn(g.user.employee_id).record(g.db, payload['nonce'])
                    flash('Check-in saved at ' + record['recorded_at'] + '.', 'success')
                    return redirect(url_for('check_in'))
                except (BadSignature, KeyError, TypeError, ValueError):
                    error, status = 'This check-in form expired. Reload before submitting.', 400
        operation_id = signer.dumps({'employee_id': g.user.employee_id, 'nonce': secrets.token_urlsafe(16)})
        history = g.db.execute('SELECT recorded_at FROM checkins WHERE employee_id=? ORDER BY id DESC LIMIT 10',
                               (g.user.employee_id,)).fetchall()
        return render_template('check_in.html', employee=employee_or_404(g.user.employee_id),
                               onsite=onsite, history=history, operation_id=operation_id, error=error), status

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.error('Database operation failed: %s', type(error).__name__)
        return render_template('error.html', code=503,
                               message='The operation could not be completed. Reopen the page and check the saved result before retrying.'), 503

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def display_error(error):
        messages = {400: error.description, 403: 'Your account is not allowed to open or change this information.',
                    404: 'This record or page is not available.', 413: 'The submitted form is too large.'}
        return render_template('error.html', code=error.code, message=messages[error.code]), error.code

    return app


if __name__ == '__main__':
    # 课堂演示绑定本机；debug=False 避免显示调试器和内部信息。
    create_app().run(host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '5000')), debug=False)
