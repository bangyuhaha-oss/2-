"""安装管理员命令。账号配置与团队配置不作为额外网站业务模块。"""
import argparse
import getpass
from contextlib import closing
from pathlib import Path
from werkzeug.security import generate_password_hash
from database import connect, initialize


def main(argv=None, database_path=None):
    """测试可显式传入临时库；命令行默认只操作本安装的数据库。"""
    parser = argparse.ArgumentParser(description='Local installation administration')
    parser.add_argument('action', choices=['accounts', 'create-account', 'set-password', 'assign-team', 'backup'])
    parser.add_argument('--username')
    parser.add_argument('--role', choices=['hr', 'supervisor', 'employee'])
    parser.add_argument('--employee-id', type=int)
    parser.add_argument('--team-id', type=int)
    parser.add_argument('--output', help='New backup file path')
    args = parser.parse_args(argv)
    path = Path(database_path) if database_path is not None else Path(__file__).parent / 'instance' / 'workforce.sqlite'
    initialize(path)
    with closing(connect(path)) as db, db:
        if args.action == 'accounts':
            for row in db.execute('SELECT username,role,employee_id,team_id FROM users ORDER BY id'):
                print(dict(row))
        elif args.action in ('create-account', 'set-password'):
            if not args.username or not args.username.strip() or len(args.username.strip()) > 120:
                parser.error('--username is required')
            args.username = args.username.strip()
            if args.action == 'create-account':
                if not args.role:
                    parser.error('--role is required')
                if args.role == 'hr' and (args.employee_id is not None or args.team_id is not None):
                    parser.error('HR access must not include employee or team scope')
                if args.role == 'employee':
                    if not args.employee_id or args.team_id is not None:
                        parser.error('Employee access requires --employee-id only; team scope comes from the employee record')
                    if not db.execute('SELECT 1 FROM employees WHERE id=?', (args.employee_id,)).fetchone():
                        parser.error('Employee not found')
                if args.role == 'supervisor':
                    if not args.team_id or args.employee_id is not None:
                        parser.error('Supervisor access requires --team-id only')
                    if not db.execute('SELECT 1 FROM teams WHERE id=?', (args.team_id,)).fetchone():
                        parser.error('Team not found')
            password = getpass.getpass('New password (at least 8 characters): ')
            if len(password) < 8:
                parser.error('Use at least 8 characters')
            if args.action == 'create-account':
                db.execute('INSERT INTO users(username,password_hash,role,employee_id,team_id) VALUES (?,?,?,?,?)',
                           (args.username, generate_password_hash(password), args.role, args.employee_id, args.team_id))
            else:
                user = db.execute('SELECT id FROM users WHERE username=?', (args.username,)).fetchone()
                if not user:
                    parser.error('Account not found')
                db.execute('UPDATE users SET password_hash=? WHERE id=?',
                           (generate_password_hash(password), user['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            print('Account saved. Passwords are never printed.')
        elif args.action == 'assign-team':
            if not args.employee_id or not args.team_id:
                parser.error('--employee-id and --team-id are required')
            # 与排班保存共用写锁，检查“没有既有排班”和更新归属是同一个原子操作。
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM employees WHERE id=?', (args.employee_id,)).fetchone():
                parser.error('Employee not found')
            if not db.execute('SELECT 1 FROM teams WHERE id=?', (args.team_id,)).fetchone():
                parser.error('Team not found')
            if db.execute('SELECT 1 FROM assignments WHERE employee_id=?', (args.employee_id,)).fetchone():
                parser.error('This employee has assignments. Resolve schedules before changing their team.')
            cursor = db.execute('UPDATE employees SET team_id=?,version=version+1 WHERE id=?',
                                (args.team_id, args.employee_id))
            if not cursor.rowcount:
                parser.error('Employee not found')
            # 员工账号只绑定本人，不保留独立的团队权限；配置变化后必须重新登录。
            db.execute("UPDATE users SET team_id=NULL WHERE employee_id=? AND role='employee'", (args.employee_id,))
            db.execute('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE employee_id=?)',
                       (args.employee_id,))
            print('Team membership saved.')
        elif args.action == 'backup':
            if not args.output:
                parser.error('--output is required')
            output = Path(args.output).resolve()
            if output.exists():
                parser.error('Backup destination must be a new file')
            with closing(connect(output)) as backup:
                db.backup(backup)
            print('Backup created:', output)


if __name__ == '__main__':
    main()
