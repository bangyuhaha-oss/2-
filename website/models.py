"""业务类：对应类图中的 User、Employee、Team、ScheduleWeek 和 CheckIn。

数据库操作全部使用参数化 SQL；每次写操作放在事务中。
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from werkzeug.security import check_password_hash

SHIFTS = {'Morning': '08:00–12:00', 'Afternoon': '12:00–16:00', 'Evening': '16:00–20:00'}
EMPLOYEE_FIELDS = ('name', 'age', 'position', 'department', 'address', 'pay_info', 'emergency_contact')
ZHENGZHOU_TIMEZONE = timezone(timedelta(hours=8))


def zhengzhou_today():
    """郑州日期不依赖安装主机的时区，也不需要额外的时区数据包。"""
    return datetime.now(ZHENGZHOU_TIMEZONE).date()


class ValidationError(ValueError):
    """业务校验失败：路由将错误显示在原表单，并保留用户输入。"""


@dataclass
class User:
    id: int
    username: str
    role: str
    employee_id: int | None
    team_id: int | None

    @staticmethod
    def from_row(row):
        """角色配置不能混用：个人身份来自员工记录，团队权限只属于主管。"""
        if not row:
            return None
        if row['role'] == 'hr' and (row['employee_id'] is not None or row['team_id'] is not None):
            return None
        if row['role'] == 'supervisor' and (row['employee_id'] is not None or row['team_id'] is None):
            return None
        if row['role'] == 'employee' and (row['employee_id'] is None or row['team_id'] is not None):
            return None
        return User(row['id'], row['username'], row['role'], row['employee_id'], row['team_id'])

    @staticmethod
    def authenticate(db, username, password):
        """FR-01：检查密码哈希；返回当前身份，密码不进入页面或会话。"""
        row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            return User.from_row(row)
        return None

    @staticmethod
    def from_session(db, token, now):
        if not isinstance(token, str) or not token or len(token) > 256:
            return None
        row = db.execute("""SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
            WHERE s.token=? AND s.expires_at>?""", (token, now)).fetchone()
        return User.from_row(row)


@dataclass
class Employee:
    id: int
    name: str
    age: int | None
    position: str
    department: str
    address: str
    pay_info: str
    emergency_contact: str
    team_id: int | None
    version: int

    @staticmethod
    def get(db, employee_id):
        row = db.execute('SELECT * FROM employees WHERE id=?', (employee_id,)).fetchone()
        return Employee(**dict(row)) if row else None

    @staticmethod
    def validate(form):
        values = {key: form.get(key, '').strip() for key in EMPLOYEE_FIELDS}
        if not values['name']:
            raise ValidationError('Name is required. Your changes have not been saved.')
        for key, value in values.items():
            if len(value) > (120 if key == 'name' else 1000):
                raise ValidationError(f'{key.replace("_", " ").title()} is too long.')
        age = values['age']
        if age and (not age.isascii() or not age.isdecimal() or len(age) > 9):
            raise ValidationError('Age must be a non-negative whole number, or left blank.')
        values['age'] = int(age) if age else None
        return values

    @staticmethod
    def create(db, values):
        """FR-02：只接收七组业务资料；重名不等于同一个人。"""
        with db:
            cursor = db.execute("""INSERT INTO employees
                (name,age,position,department,address,pay_info,emergency_contact)
                VALUES (?,?,?,?,?,?,?)""", tuple(values[key] for key in EMPLOYEE_FIELDS))
        return cursor.lastrowid

    def update(self, db, values, version):
        """FR-04：版本号避免旧表单覆盖另一人的新修改。"""
        with db:
            cursor = db.execute("""UPDATE employees SET name=?,age=?,position=?,department=?,
                address=?,pay_info=?,emergency_contact=?,version=version+1
                WHERE id=? AND version=?""",
                tuple(values[key] for key in EMPLOYEE_FIELDS) + (self.id, version))
            if cursor.rowcount != 1:
                raise ValidationError('This record changed. Reopen it before saving again.')

    def delete(self, db, version):
        """FR-05：真实删除；外键同步删除关联账号、会话、排班条目和签到。"""
        with db:
            cursor = db.execute('DELETE FROM employees WHERE id=? AND version=?', (self.id, version))
            if cursor.rowcount != 1:
                raise ValidationError('This record changed or was deleted. Reopen the employee list.')


@dataclass
class Team:
    id: int
    name: str

    def members(self, db):
        """主管只拿到排班所需字段，完整个人资料不会传入主管模板。"""
        return db.execute('SELECT id,name,position,department FROM employees WHERE team_id=? ORDER BY id',
                          (self.id,)).fetchall()


@dataclass
class ScheduleWeek:
    team_id: int
    week: str

    def load(self, db):
        week = db.execute('SELECT * FROM schedule_weeks WHERE team_id=? AND week=?',
                          (self.team_id, self.week)).fetchone()
        rows = db.execute("""SELECT a.* FROM assignments a JOIN schedule_weeks w ON w.id=a.schedule_id
            WHERE w.team_id=? AND w.week=?""", (self.team_id, self.week)).fetchall()
        selected = {}
        for row in rows:
            selected.setdefault((row['employee_id'], row['day']), []).append(row['shift'])
        return week, selected

    def save(self, db, members, days, form, version):
        """FR-07/08：先校验整个团队周计划，再一次性写入；拒绝部分完成。"""
        with db:
            db.execute('BEGIN IMMEDIATE')
            # 加锁后再次读取成员，防止删除或安装配置在校验和保存之间改变团队范围。
            current_members = Team(self.team_id, '').members(db)
            if {member['id'] for member in current_members} != {member['id'] for member in members}:
                raise ValidationError('Team membership changed. Reload the week before saving again.')
            if not current_members:
                raise ValidationError('There are no employees in this team.')
            allowed_keys = {f'shift_{m["id"]}_{day}' for m in current_members for day in days}
            if any(key.startswith('shift_') and key not in allowed_keys for key in form):
                raise ValidationError('An assignment is outside your team or selected week.')
            assignments = []
            member_errors = {}
            for employee in current_members:
                working_days = 0
                errors = []
                for day in days:
                    shifts = form.getlist(f'shift_{employee["id"]}_{day}')
                    if shifts:
                        if len(shifts) != 2 or len(set(shifts)) != 2 or any(s not in SHIFTS for s in shifts):
                            errors.append(f'{day}: select two different fixed shifts.')
                        working_days += 1
                        assignments.extend((employee['id'], day, shift) for shift in shifts)
                if working_days != 5:
                    errors.append(f'Select exactly five working days (currently {working_days}).')
                if errors:
                    member_errors[employee['id']] = errors
            if member_errors:
                problem = ValidationError('The complete week was not saved. Review the member issues below.')
                problem.member_errors = member_errors
                raise problem
            current = db.execute('SELECT * FROM schedule_weeks WHERE team_id=? AND week=?',
                                 (self.team_id, self.week)).fetchone()
            if (current['version'] if current else 0) != version:
                raise ValidationError('This schedule changed. Reload the week before saving again.')
            if current:
                schedule_id = current['id']
                db.execute('UPDATE schedule_weeks SET version=version+1 WHERE id=?', (schedule_id,))
                db.execute('DELETE FROM assignments WHERE schedule_id=?', (schedule_id,))
            else:
                schedule_id = db.execute('INSERT INTO schedule_weeks(team_id,week) VALUES (?,?)',
                                         (self.team_id, self.week)).lastrowid
            db.executemany('INSERT INTO assignments VALUES (?,?,?,?)',
                           [(schedule_id, *row) for row in assignments])


@dataclass
class CheckIn:
    employee_id: int

    def record(self, db, operation_id):
        """FR-10：同一个提交标识重复发送时，返回原记录，避免重试重复签到。"""
        timestamp = datetime.now(ZHENGZHOU_TIMEZONE).isoformat(timespec='seconds')
        with db:
            db.execute('INSERT OR IGNORE INTO checkins(employee_id,operation_id,recorded_at) VALUES (?,?,?)',
                       (self.employee_id, operation_id, timestamp))
        return db.execute('SELECT * FROM checkins WHERE employee_id=? AND operation_id=?',
                          (self.employee_id, operation_id)).fetchone()
