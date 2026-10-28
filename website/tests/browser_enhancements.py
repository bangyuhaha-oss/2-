"""增强版真实浏览器流程与版面截图：临时库、关闭页面JavaScript、无需外部资源。"""
import argparse
import csv
import hashlib
import io
import importlib.metadata
import json
import re
import sqlite3
import sys
import tempfile
import threading
import traceback
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import RELEASE, create_app
from models import zhengzhou_today


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    evidence = ROOT.parent / 'evidence'
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    shots = evidence / 'screenshots' / ('enhancement_preview_' if args.preview else 'enhancement_')
    shots = shots.parent / (shots.name + stamp)
    shots.mkdir(parents=True, exist_ok=True)
    report = {'release': RELEASE, 'run_id': stamp, 'mode': 'early UI preview' if args.preview else 'final browser verification',
              'javascript_enabled': False, 'database': 'isolated temporary SQLite',
              'checks': [], 'screenshots': [], 'print_pdfs': [], 'layouts': [], 'console_errors': [], 'script_requests': [],
              'playwright_version': importlib.metadata.version('playwright'),
              'boundary': 'Browser automation and viewport/print-media emulation, not independent user/physical phone/store or teacher acceptance.'}
    def application_fingerprints():
        paths = [ROOT / name for name in ['app.py', 'models.py', 'workspace.py', 'database.py', 'manage.py']]
        paths += [path for area in [ROOT / 'templates', ROOT / 'static'] for path in area.glob('*')
                  if path.suffix in {'.html', '.css'}]
        return {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}

    source_before = application_fingerprints()
    with tempfile.TemporaryDirectory(prefix='meiti_enhancement_browser_') as temporary:
        db_path = Path(temporary) / 'browser.sqlite'
        application = create_app({'DATABASE': str(db_path), 'TESTING': True, 'SECRET_KEY': 'isolated-browser-secret'})
        # 浏览器会预建连接；线程服务避免一个空闲连接阻塞其他角色的页面。
        server = make_server('127.0.0.1', 0, application, threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def business_snapshot():
            with closing(sqlite3.connect(db_path)) as db:
                return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1,2').fetchall()
                        for table in ('employees', 'schedule_weeks', 'assignments', 'checkins')}

        try:
            with sync_playwright() as playwright:
                choices = sorted((Path.home() / 'AppData/Local/ms-playwright').glob('chromium-*/chrome-win*/chrome.exe'), reverse=True)
                browser = playwright.chromium.launch(headless=True, executable_path=str(choices[0]) if choices else None)
                report['browser_version'] = browser.version
                contexts = []

                def context():
                    instance = browser.new_context(viewport={'width': 1440, 'height': 1000}, java_script_enabled=False,
                                                   accept_downloads=True)
                    contexts.append(instance)
                    page = instance.new_page()
                    page.set_default_timeout(45000)
                    page.set_default_navigation_timeout(45000)
                    page.on('pageerror', lambda error: report['console_errors'].append(str(error)))
                    page.on('request', lambda req: report['script_requests'].append(req.url) if req.resource_type == 'script' else None)
                    return page

                def check(identifier, condition, observation):
                    report['checks'].append({'id': identifier, 'status': 'PASS' if condition else 'FAIL', 'observation': observation})
                    assert condition, identifier + ': ' + observation

                def go(page, path, expected=200):
                    page.bring_to_front()
                    response = page.goto(base + path)
                    assert response.status == expected, (path, response.status)
                    assert "script-src 'none'" in response.headers.get('content-security-policy', '')
                    return response

                def navigate(page, locator, mouse=False):
                    # 禁JS时不依赖驱动的rAF稳定性轮询；用真实鼠标或键盘激活HTML控件。
                    page.bring_to_front()
                    assert locator.is_visible() and locator.is_enabled()
                    locator.focus()
                    with page.expect_navigation(wait_until='domcontentloaded'):
                        if mouse:
                            box = locator.bounding_box()
                            assert box and box['y'] >= 0 and box['y'] + box['height'] <= page.viewport_size['height']
                            page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        else:
                            locator.press('Enter')

                def checkbox(locator, checked):
                    # :checked集合在切换后会改变，先固定到内部name/value对应的同一个输入。
                    name, value = locator.get_attribute('name'), locator.get_attribute('value')
                    locator = locator.page.locator(f'input[name="{name}"][value="{value}"]')
                    assert locator.count() == 1
                    assert locator.is_visible() and locator.is_enabled()
                    if locator.is_checked() != checked:
                        locator.focus()
                        locator.press('Space')
                    assert locator.is_checked() == checked

                def login(page, username):
                    go(page, '/login')
                    page.locator('#username').fill(username)
                    page.locator('#password').fill('Demo!2026')
                    navigate(page, page.locator('button[type=submit]'), mouse=True)
                    page.wait_for_url('**/dashboard', wait_until='domcontentloaded')

                def shot(page, name):
                    # 截取有限CSS淡入结束态，避免把0.4透明的入场帧当成最终配色。
                    page.screenshot(path=str(shots / name), full_page=True, animations='disabled')
                    path = shots / name
                    report['screenshots'].append({'path': path.relative_to(ROOT.parent).as_posix(), 'sha256': sha(path),
                                                  'viewport': page.viewport_size, 'url_path': page.url.removeprefix(base)})
                    measurements = page.evaluate('''() => ({width:innerWidth, document:document.documentElement.scrollWidth,
                        main:document.querySelector('main')?.getBoundingClientRect().width,
                        typography:{body:getComputedStyle(document.body).fontSize,
                            table:document.querySelector('tbody td')?getComputedStyle(document.querySelector('tbody td')).fontSize:null,
                            helper:document.querySelector('small')?getComputedStyle(document.querySelector('small')).fontSize:null},
                        horizontalTables:[...document.querySelectorAll('.table-scroll')].map(e=>({client:e.clientWidth,scroll:e.scrollWidth}))})''')
                    report['layouts'].append({'screenshot': name, **measurements})
                    assert measurements['document'] <= measurements['width'] + 1, ('page overflow', name, measurements)

                def print_pdf(page, name):
                    path = shots / name
                    page.pdf(path=str(path), landscape=True, print_background=True)
                    report['print_pdfs'].append({'path': path.relative_to(ROOT.parent).as_posix(), 'sha256': sha(path)})

                hr, supervisor, employee = context(), context(), context()
                for page, username, name in [(hr, 'hr', '01_hr_dashboard_desktop.png'),
                                              (supervisor, 'supervisor', '02_supervisor_dashboard_desktop.png'),
                                              (employee, 'employee', '03_employee_dashboard_desktop.png')]:
                    login(page, username)
                    shot(page, name)
                employee.set_viewport_size({'width': 390, 'height': 844})
                shot(employee, '04_employee_dashboard_mobile.png')
                checkin_link = employee.get_by_role('link', name='Go to check-in', exact=False)
                box = checkin_link.bounding_box()
                assert box and box['y'] >= 0 and box['y'] + box['height'] <= 844, 'Mobile check-in entry must be in first viewport'
                check('UI01', True, 'Three authenticated role dashboards and390px employee layout rendered without page overflow.')
                if not args.preview:
                    employee.set_viewport_size({'width': 1440, 'height': 1000})
                    go(hr, '/employees')
                    hr.locator('#name').fill('Alex Chen')
                    navigate(hr, hr.locator('form[method=get]').first.locator('button[type=submit]'))
                    assert hr.locator('tbody tr').count() == 2
                    shot(hr, '05_hr_directory_filtered.png')
                    check('UI02', True, 'No-JS directory search found two distinct same-name records.')
                    go(hr, '/employees?department=Service&sort=name_desc&page=2')
                    navigate(hr, hr.locator('tbody a').first)
                    shot(hr, '06_hr_detail_context.png')
                    navigate(hr, hr.get_by_role('link', name='Edit employee', exact=True))
                    navigate(hr, hr.get_by_role('link', name='Cancel', exact=True))
                    navigate(hr, hr.get_by_role('link', name='Back to workforce', exact=False))
                    check('UI03', all(part in hr.url for part in ['department=Service', 'sort=name_desc', 'page=2']),
                          'Detail/edit/cancel/back retained whitelisted filter, sort and page context.')
                    go(hr, '/employees')
                    with hr.expect_download() as download:
                        hr.locator('a[href*="/employees/export.csv"]').press('Enter')
                    records = list(csv.reader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
                    check('UI04', len(records) == 501, 'HR downloaded500 matching records rather than current20 rows.')
                    go(hr, '/employees/new')
                    injected_name = '=Browser "Quoted", Employee'
                    hr.locator('#name').fill(injected_name)
                    hr.locator('#address').fill('@Address, "line one"\nLine two')
                    hr.locator('#pay_info').fill('+25 CNY, "text only"')
                    navigate(hr, hr.get_by_role('button', name='Save employee', exact=True))
                    record_path = hr.url.removeprefix(base).split('?')[0]
                    record_id = record_path.rsplit('/', 1)[-1]
                    go(hr, '/employees?name=' + quote(injected_name))
                    with hr.expect_download() as download:
                        hr.locator('a[href*="/employees/export.csv"]').press('Enter')
                    downloaded = Path(download.value.path()).read_bytes()
                    values = list(csv.reader(io.StringIO(downloaded.decode('utf-8-sig'))))
                    assert downloaded.startswith(b'\xef\xbb\xbf') and len(values) == 2
                    with closing(sqlite3.connect(db_path)) as db:
                        stored = db.execute('SELECT name,address,pay_info FROM employees WHERE id=?', (record_id,)).fetchone()
                    # HTML原生表单可把textarea的LF规范化为CRLF；CSV必须逐字保留实际存储值。
                    assert stored[0] == injected_name
                    assert stored[1].replace('\r\n', '\n') == '@Address, "line one"\nLine two'
                    assert stored[2] == '+25 CNY, "text only"'
                    assert values[1][1] == "'" + stored[0]
                    assert values[1][5] == "'" + stored[1]
                    assert values[1][6] == "'" + stored[2]
                    report['csv_multiline_roundtrip'] = {'native_form_newline': 'CRLF' if '\r\n' in stored[1] else 'LF',
                                                         'csv_matches_database_exactly': True, 'formula_prefix_protected': True}
                    go(hr, '/employees/' + record_id)
                    navigate(hr, hr.get_by_role('link', name='Edit employee', exact=True))
                    hr.locator('#pay_info').fill('Browser edit saved')
                    navigate(hr, hr.get_by_role('button', name='Save changes', exact=True))
                    assert hr.get_by_text('Browser edit saved', exact=True).is_visible()
                    navigate(hr, hr.get_by_role('link', name='Delete employee', exact=True))
                    hr.locator('#confirmation').fill(injected_name)
                    navigate(hr, hr.get_by_role('button', name='Permanently delete employee', exact=True))
                    with closing(sqlite3.connect(db_path)) as db:
                        assert db.execute('SELECT COUNT(*) FROM employees').fetchone()[0] == 500
                        assert db.execute('SELECT COUNT(*) FROM employees WHERE id=?', (record_id,)).fetchone()[0] == 0
                    check('UI13', True, 'HR created, edited and confirmed deletion through no-JS forms; real CSV download escaped formula text and preserved commas, quotes and newlines.')
                    go(hr, '/dashboard')
                    hr.set_viewport_size({'width': 1366, 'height': 768})
                    shot(hr, '19_hr_dashboard_projection.png')
                    hr.set_viewport_size({'width': 1440, 'height': 1000})
                    go(hr, '/employees')
                    shot(hr, '07_hr_directory_desktop.png')
                    hr.set_viewport_size({'width': 390, 'height': 844})
                    shot(hr, '08_hr_directory_mobile.png')
                    hr.set_viewport_size({'width': 1440, 'height': 1000})

                    go(supervisor, '/team-week')
                    shot(supervisor, '09_team_week_desktop.png')
                    supervisor.emulate_media(media='print')
                    shot(supervisor, '10_team_week_print_media.png')
                    print_pdf(supervisor, 'team_week_print.pdf')
                    supervisor.emulate_media(media='screen')
                    before = business_snapshot()
                    today = zhengzhou_today()
                    monday = today - timedelta(days=today.weekday())
                    next_week = (monday + timedelta(days=7)).isoformat()
                    go(supervisor, '/team-schedule?week=' + next_week + '&draft=previous')
                    check('UI05', business_snapshot() == before, 'Loading previous week only prefilled HTML and left all business rows unchanged.')
                    shot(supervisor, '11_schedule_previous_draft.png')
                    review = supervisor.locator('input[name="draft_reviewed"]')
                    assert review.count() == 1, 'Explicit review checkbox must exist for a loaded draft'
                    checkbox(review, True)
                    navigate(supervisor, supervisor.get_by_role('button', name='Save complete week', exact=True))
                    check('UI06', supervisor.locator('.notice.success').count() > 0, 'Supervisor explicitly confirmed and saved a complete copied week.')
                    saved_business = business_snapshot()
                    checkbox(supervisor.locator('input[type=checkbox]:checked').first, False)
                    navigate(supervisor, supervisor.get_by_role('button', name='Save complete week', exact=True))
                    check('UI07', supervisor.locator('[role=alert]').count() > 0 and
                          'Unsaved' in supervisor.locator('main').inner_text() and business_snapshot() == saved_business,
                          'Invalid member schedule retained choices and explicit unsaved status without changing any saved business row.')
                    shot(supervisor, '12_schedule_member_errors.png')
                    supervisor.set_viewport_size({'width': 390, 'height': 844})
                    shot(supervisor, '13_schedule_mobile.png')
                    supervisor.set_viewport_size({'width': 1440, 'height': 1000})
                    go(supervisor, '/employees/export.csv', 403)
                    go(supervisor, '/team-week?team_id=2', 403)
                    go(hr, '/team-week/print', 403)
                    check('UI08', True, 'Browser direct requests enforce CSV role, own-team and print role boundaries.')

                    # 日期边界/分页fixture只写这个浏览器运行的临时库，均为明确虚构记录。
                    date_text = today.isoformat()
                    stamps = [date_text + 'T00:00:00+08:00', date_text + 'T23:59:59+08:00']
                    stamps += [date_text + f'T12:30:{second:02d}+08:00' for second in range(23)]
                    with closing(sqlite3.connect(db_path)) as db, db:
                        db.executemany('INSERT INTO checkins(employee_id,operation_id,recorded_at) VALUES (1,?,?)',
                                       [('browser-fixture-' + str(index), stamp) for index, stamp in enumerate(stamps)])
                        db.execute('INSERT INTO checkins(employee_id,operation_id,recorded_at) VALUES (1,?,?)',
                                   ('browser-earlier-fixture', (today - timedelta(days=1)).isoformat() + 'T10:00:00+08:00'))
                        db.execute('INSERT INTO checkins(employee_id,operation_id,recorded_at) VALUES (2,?,?)',
                                   ('browser-other-person-fixture', date_text + 'T10:00:00+08:00'))
                    report['history_fixture'] = '25 own records today including00:00/23:59:59, one own previous day, one other-person record; isolated synthetic fixture.'
                    go(employee, '/my-schedule')
                    shot(employee, '14_personal_week_desktop.png')
                    employee.set_viewport_size({'width': 390, 'height': 844})
                    shot(employee, '15_personal_week_mobile.png')
                    go(employee, '/check-in')
                    navigate(employee, employee.get_by_role('button', name='Check in now', exact=False))
                    check('UI09', employee.locator('.notice.success').count() > 0, 'Employee completed labelled loopback simulation and received saved result.')
                    go(employee, '/my-checkins?from=' + date_text + '&to=' + date_text)
                    assert employee.locator('tbody tr').count() == 20
                    navigate(employee, employee.get_by_role('link', name='Next', exact=False))
                    assert employee.locator('tbody tr').count() == 6
                    shot(employee, '16_checkin_history_mobile.png')
                    with employee.expect_download() as download:
                        employee.locator('a[href*="/my-checkins/export.csv"]').press('Enter')
                    records = list(csv.reader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
                    assert len(records) == 27 and all(row[1] == '1' for row in records[1:])
                    assert any('T00:00:00' in row[2] for row in records[1:])
                    assert any('T23:59:59' in row[2] for row in records[1:])
                    navigate(employee, employee.get_by_role('link', name='Clear', exact=True))
                    assert employee.get_by_text('27', exact=True).is_visible()
                    go(employee, '/my-checkins?from=' + (today + timedelta(days=1)).isoformat() + '&to=' + date_text, 400)
                    check('UI10', True, 'Own-date history paged20+6 rows; CSV all26 matches includes both date endpoints and own identity only; clear shows27, reversed range rejected.')
                    employee.set_viewport_size({'width': 1440, 'height': 1000})
                    go(employee, '/my-checkins?from=' + date_text + '&to=' + date_text)
                    shot(employee, '20_checkin_history_desktop.png')
                    go(employee, '/my-checkins/print?from=' + date_text + '&to=' + date_text)
                    assert employee.locator('tbody tr').count() == 26
                    employee.emulate_media(media='print')
                    shot(employee, '17_checkin_history_print_media.png')
                    print_pdf(employee, 'checkin_history_print.pdf')
                    employee.emulate_media(media='screen')
                    go(employee, '/my-schedule/print')
                    employee.emulate_media(media='print')
                    shot(employee, '18_personal_week_print_media.png')
                    print_pdf(employee, 'personal_week_print.pdf')
                    employee.emulate_media(media='screen')
                    go(employee, '/my-checkins/export.csv?employee_id=2', 403)
                    go(employee, '/my-schedule/print?employee_id=2', 403)
                    check('UI11', True, 'Personal print views rendered; forged employee history export and schedule print denied.')
                    employee.emulate_media(reduced_motion='reduce')
                    go(employee, '/dashboard')
                    check('UI12', not report['console_errors'] and not report['script_requests'],
                          'All flows ran with page JS disabled, no script resource requests or browser page errors; reduced-motion preference usable.')
                for instance in contexts:
                    instance.close()
                browser.close()
        except Exception:
            report['status'] = 'FAIL'
            report['failure'] = traceback.format_exc()
            (evidence / 'website_enhancement_browser_failed.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            raise
        finally:
            server.shutdown()
            worker.join(timeout=10)
            server.server_close()
    report['status'] = 'PASS'
    report['total'] = len(report['checks'])
    report['source_sha256'] = application_fingerprints()
    assert report['source_sha256'] == source_before, 'Runtime source changed during browser run; recapture required.'
    report['test_script_sha256'] = sha(Path(__file__))
    output = evidence / ('website_enhancement_preview.json' if args.preview else 'website_enhancement_browser.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(output), 'screenshots': str(shots), 'total': report['total'], 'status': report['status']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
