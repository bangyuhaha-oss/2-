"""数据库入口：连接、建表和虚构演示数据。只使用 Python 标准库 sqlite3。"""
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from werkzeug.security import generate_password_hash
from models import zhengzhou_today


def connect(path):
    """每次请求使用一个连接；外键保证删除操作不会留下孤立数据。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(path):
    """首次启动建表。已存在的数据库不会重新填充或覆盖。"""
    with closing(connect(path)) as db, db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, age INTEGER CHECK(age IS NULL OR age >= 0),
            position TEXT NOT NULL DEFAULT '', department TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '', pay_info TEXT NOT NULL DEFAULT '',
            emergency_contact TEXT NOT NULL DEFAULT '',
            team_id INTEGER REFERENCES teams(id), version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('hr','supervisor','employee')),
            employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
            team_id INTEGER REFERENCES teams(id));
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS schedule_weeks (
            id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL REFERENCES teams(id),
            week TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            UNIQUE(team_id, week));
        CREATE TABLE IF NOT EXISTS assignments (
            schedule_id INTEGER NOT NULL REFERENCES schedule_weeks(id) ON DELETE CASCADE,
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            day TEXT NOT NULL, shift TEXT NOT NULL
                CHECK(shift IN ('Morning','Afternoon','Evening')),
            PRIMARY KEY(schedule_id, employee_id, day, shift));
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL
                REFERENCES employees(id) ON DELETE CASCADE,
            operation_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
            UNIQUE(employee_id, operation_id));
        CREATE TABLE IF NOT EXISTS login_failures (
            source TEXT NOT NULL, username TEXT NOT NULL, attempted_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if not db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
            seed_demo(db)


def seed_demo(db):
    """500 条虚构员工、两支小团队和手工写定的示例排班；不是自动排班功能。"""
    db.executemany("INSERT INTO teams VALUES (?, ?)",
                   [(1, 'Retail Operations'), (2, 'Customer Services')])
    for number in range(1, 501):
        name = {1: 'Lin Wei', 2: 'Chen Rui', 3: 'Alex Chen', 4: 'Alex Chen'}.get(
            number, f'Demo Employee {number:03}')
        team = 1 if number in (1, 3, 4) else 2 if number in (2, 5) else None
        department = 'Retail' if number % 2 else 'Service'
        db.execute("""INSERT INTO employees
            (name,age,position,department,address,pay_info,emergency_contact,team_id)
            VALUES (?,?,?,?,?,?,?,?)""",
            (name, 20 + number % 35, 'Store Assistant', department,
             'Fictional address', 'Hourly wage: 25 CNY', 'Fictional contact', team))
    accounts = [('hr', 'hr', None, None), ('supervisor', 'supervisor', None, 1),
                ('supervisor2', 'supervisor', None, 2),
                ('employee', 'employee', 1, None), ('employee2', 'employee', 2, None)]
    password_hash = generate_password_hash('Demo!2026')
    db.executemany("""INSERT INTO users
        (username,password_hash,role,employee_id,team_id) VALUES (?,?,?,?,?)""",
        [(name, password_hash, role, employee, team)
         for name, role, employee, team in accounts])
    today = zhengzhou_today()
    monday = today - timedelta(days=today.weekday())
    # 展示数据在当前周和下一周各放一份，安装后不会按日期自动重建。
    for offset in (0, 7):
        week = monday + timedelta(days=offset)
        for team, members in ((1, (1, 3, 4)), (2, (2, 5))):
            cursor = db.execute("INSERT INTO schedule_weeks(team_id,week) VALUES (?,?)",
                                (team, week.isoformat()))
            for employee in members:
                for day in range(5):
                    for shift in ('Morning', 'Afternoon'):
                        db.execute("INSERT INTO assignments VALUES (?,?,?,?)",
                                   (cursor.lastrowid, employee,
                                    (week + timedelta(days=day)).isoformat(), shift))
    db.execute("INSERT INTO settings VALUES ('initialized','1')")
