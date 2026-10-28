"""工作台的只读查询、日期摘要和安全CSV；不增加业务模型或数据表。"""
import csv
import io
import re
import unicodedata
from datetime import date, datetime, time, timedelta

from flask import Response
from models import SHIFTS, ValidationError, ZHENGZHOU_TIMEZONE

SORT_OPTIONS = {'id_asc': 'Record number', 'name_asc': 'Name · A to Z',
                'name_desc': 'Name · Z to A', 'department_asc': 'Department · A to Z',
                'position_asc': 'Position · A to Z'}
SORT_SQL = {'id_asc': 'id ASC', 'name_asc': 'name COLLATE NOCASE ASC, id ASC',
            'name_desc': 'name COLLATE NOCASE DESC, id ASC',
            'department_asc': 'department COLLATE NOCASE ASC, id ASC',
            'position_asc': 'position COLLATE NOCASE ASC, id ASC'}
SHIFT_STARTS = {'Morning': 8, 'Afternoon': 12, 'Evening': 16}


def zhengzhou_now():
    """今日与下次班次统一使用郑州时间；测试可替换此时钟。"""
    return datetime.now(ZHENGZHOU_TIMEZONE)


def page_number(value, count=None):
    """页码必须为整数，实际读取时限制在现有结果页范围。"""
    try:
        page = max(1, int(value))
    except (TypeError, ValueError):
        raise ValidationError('The page number must be a whole number.')
    return min(page, max(1, (count + 19) // 20)) if count is not None else page


def directory_filters(arguments, user):
    """只解析白名单目录参数；相同参数用于列表、CSV及资料返回链接。"""
    values = {'name': arguments.get('name', '').strip()[:120],
              'department': '', 'position': '', 'department_empty': False, 'position_empty': False,
              'sort': arguments.get('sort', 'id_asc')}
    if values['sort'] not in SORT_SQL:
        raise ValidationError('Select one of the available directory sort orders.')
    conditions, params = [], []
    if user.role == 'supervisor':
        conditions.append('team_id=?')
        params.append(user.team_id)
    else:
        for field in ('department', 'position'):
            values[field] = arguments.get(field, '')[:1000]
            empty = arguments.get(field + '_empty', '')
            if empty not in ('', '0', '1'):
                raise ValidationError('Use the available empty-field filter.')
            values[field + '_empty'] = empty == '1'
            if values[field] and values[field + '_empty']:
                raise ValidationError('Choose a specific ' + field + ' or its empty-field filter, not both.')
            if values[field + '_empty']:
                conditions.append(field + "=''")
            elif values[field]:
                conditions.append(field + '=?')
                params.append(values[field])
    if values['name']:
        literal = values['name'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append("name LIKE ? ESCAPE '\\'")
        params.append('%' + literal + '%')
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    context = {'name': values['name'], 'sort': values['sort'],
               'page': page_number(arguments.get('page', '1'))}
    if user.role == 'hr':
        for field in ('department', 'position'):
            context[field] = values[field]
            if values[field + '_empty']:
                context[field + '_empty'] = '1'
    return values, where, params, context


def workforce_summary(db, team_id=None):
    """按真实原始值分组；空字段和名为Unspecified的真实部门始终分开。"""
    where, params = (' WHERE team_id=?', (team_id,)) if team_id is not None else ('', ())
    total = db.execute('SELECT COUNT(*) FROM employees' + where, params).fetchone()[0]
    grouped = {}
    for field in ('department', 'position'):
        rows = db.execute(f'SELECT {field} AS value, COUNT(*) AS total FROM employees' +
                          where + f' GROUP BY {field} ORDER BY total DESC, {field}', params).fetchall()
        grouped[field] = [{'value': row['value'], 'label': row['value'] or 'Not provided (empty)',
                           'is_empty': row['value'] == '', 'total': row['total']} for row in rows]
    return total, grouped


def directory_options(db):
    """下拉框只使用实际非空值；空值另用独立筛选标志，不占用任何文本。"""
    return {field + '_options': [row[0] for row in db.execute(
        f"SELECT DISTINCT {field} FROM employees WHERE {field}<>'' ORDER BY {field} COLLATE NOCASE")]
        for field in ('department', 'position')}


def safe_csv_cell(value):
    """保留内容并给潜在公式加单引号，包括前导空白或控制字符。"""
    text = '' if value is None else str(value)
    probe = text
    while probe and (probe[0].isspace() or unicodedata.category(probe[0]) in {'Cc', 'Cf'}):
        probe = probe[1:]
    if (probe.startswith(('=', '+', '-', '@')) or
            (text and unicodedata.category(text[0]) in {'Cc', 'Cf'})):
        return "'" + text
    return text


def csv_download(headers, rows, filename):
    """所有匹配行写入带BOM的CSV；仅由已鉴权的HR/本人路由调用。"""
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(headers)
    count = 0
    for row in rows:
        writer.writerow([safe_csv_cell(value) for value in row])
        count += 1
    response = Response(output.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.headers['X-Total-Records'] = str(count)
    return response


def week_links(days):
    monday = date.fromisoformat(days[0])
    today = zhengzhou_now().date()
    return {'previous': (monday - timedelta(days=7)).isoformat(),
            'following': (monday + timedelta(days=7)).isoformat(),
            'current_week': (today - timedelta(days=today.weekday())).isoformat()}


def team_week_summary(members, days, selected):
    """每个固定班为4小时；只汇总计划，不推断实际出勤或工资。"""
    summaries, daily_counts = [], {day: 0 for day in days}
    daily_shift_counts = {day: {shift: 0 for shift in SHIFTS} for day in days}
    daily_people = {day: 0 for day in days}
    for member in members:
        count, working_days = 0, 0
        for day in days:
            shifts = selected.get((member['id'], day), [])
            count += len(shifts)
            working_days += bool(shifts)
            daily_counts[day] += len(shifts)
            daily_people[day] += bool(shifts)
            for shift in SHIFTS:
                daily_shift_counts[day][shift] += shift in shifts
        summaries.append({'id': member['id'], 'name': member['name'], 'working_days': working_days,
                          'shift_count': count, 'hours': count * 4})
    total = sum(row['shift_count'] for row in summaries)
    return {'member_summaries': summaries, 'daily_counts': daily_counts,
            'daily_shift_counts': daily_shift_counts, 'daily_people': daily_people,
            'total_shifts': total, 'total_hours': total * 4,
            'summary': {'total_shifts': total, 'total_hours': total * 4,
                        'working_days': sum(row['working_days'] for row in summaries)}}


def personal_week(db, employee_id, days):
    """所选周与真正今日分开读取；下次只看当前起90天内的已保存班次。"""
    rows = db.execute('SELECT DISTINCT day,shift FROM assignments WHERE employee_id=? AND day BETWEEN ? AND ?',
                      (employee_id, days[0], days[-1])).fetchall()
    selected = {day: [shift for shift in SHIFTS if any(row['day'] == day and row['shift'] == shift for row in rows)]
                for day in days}
    now = zhengzhou_now()
    today = now.date().isoformat()
    today_rows = db.execute('SELECT DISTINCT shift FROM assignments WHERE employee_id=? AND day=?',
                            (employee_id, today)).fetchall()
    today_shifts = [shift for shift in SHIFTS if any(row['shift'] == shift for row in today_rows)]
    search_end = now + timedelta(days=90)
    future = db.execute('SELECT DISTINCT day,shift FROM assignments WHERE employee_id=? AND day BETWEEN ? AND ? ORDER BY day',
                         (employee_id, today, search_end.date().isoformat())).fetchall()
    upcoming = []
    for row in future:
        start_hour = SHIFT_STARTS[row['shift']]
        start = datetime.combine(date.fromisoformat(row['day']), time(start_hour), ZHENGZHOU_TIMEZONE)
        if now <= start <= search_end:
            upcoming.append((start, row['shift']))
    next_shift = None
    if upcoming:
        start, shift = min(upcoming)
        next_shift = {'day': start.date().isoformat(), 'shift': shift,
                      'start': start.strftime('%H:%M'), 'end': (start + timedelta(hours=4)).strftime('%H:%M')}
    return {'days': days, 'selected': selected, 'assignment_count': len(rows),
            'working_days': sum(bool(shifts) for shifts in selected.values()), 'total_hours': len(rows) * 4,
            'today': today, 'today_shifts': today_shifts, 'next_shift': next_shift,
            'next_search_end': search_end.date().isoformat(),
            'next_search_start_at': now.strftime('%Y-%m-%d %H:%M:%S') + ' UTC+08:00',
            'next_search_end_at': search_end.strftime('%Y-%m-%d %H:%M:%S') + ' UTC+08:00',
            **week_links(days)}


def checkin_filters(arguments, employee_id):
    """严格ISO本地日期，包含起止两天；本人身份只来自已验证账号。"""
    values = {}
    for key in ('from', 'to'):
        raw = arguments.get(key, '').strip()
        if raw:
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
                    raise ValueError
                date.fromisoformat(raw)
            except ValueError:
                raise ValidationError('Select a valid from/to date in YYYY-MM-DD format.')
        values[key] = raw
    if values['from'] and values['to'] and values['from'] > values['to']:
        raise ValidationError('The from date must not be after the to date.')
    conditions, params = ['employee_id=?'], [employee_id]
    for key, operator in (('from', '>='), ('to', '<=')):
        if values[key]:
            conditions.append(f'substr(recorded_at,1,10) {operator} ?')
            params.append(values[key])
    return ' WHERE ' + ' AND '.join(conditions), params, values
