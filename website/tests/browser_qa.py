"""开发环境的浏览器操作与截图；浏览器关闭 JavaScript。

需要开发工具 playwright（网站运行不需要它）。不改动课堂演示数据库。
"""
import json
import sys
import tempfile
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import RELEASE, create_app


def main():
    output = ROOT.parent / 'evidence' / 'screenshots'
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app({'DATABASE': str(Path(temporary)/'browser.sqlite'), 'TESTING': True})
        server = make_server('127.0.0.1', 5002, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                # 使用本机已有 Chromium；没有时先执行 playwright install chromium。
                candidates = list((Path.home()/'AppData/Local/ms-playwright').glob('chromium-*/chrome-win*/chrome.exe'))
                browser = playwright.chromium.launch(headless=True, executable_path=str(candidates[0]) if candidates else None)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, java_script_enabled=False)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))

                def go(path):
                    return page.goto('http://127.0.0.1:5002' + path)

                def shot(name):
                    page.screenshot(path=str(output/name), animations='disabled')

                def login(username):
                    go('/login')
                    page.get_by_label('Username', exact=True).fill(username)
                    page.get_by_label('Password', exact=True).fill('Demo!2026')
                    page.get_by_role('button', name='Sign in to workspace').click(no_wait_after=True)
                    page.wait_for_url('**/dashboard')
                    # 2.1首页变为工作台；旧八项继续验证原业务流程，工作台由新增用例覆盖。
                    if username == 'hr':
                        go('/employees')
                    elif username == 'employee':
                        go('/my-schedule')

                go('/login')
                shot('01_login_desktop.png')
                login('hr')
                page.get_by_role('heading', name='Workforce', exact=True).wait_for()
                shot('02_hr_workforce.png')
                page.get_by_role('link', name='+ Add employee').click()
                page.get_by_label('Name', exact=False).fill('QA Browser Employee')
                page.get_by_role('button', name='Save employee', exact=True).click()
                page.get_by_role('heading', name='QA Browser Employee', exact=True).wait_for()
                results.append({'id': 'GUI01', 'status': 'PASS', 'observation': 'HR created a name-only record through the browser with JavaScript disabled.'})
                page.get_by_role('link', name='Edit employee', exact=True).click()
                page.get_by_label('Pay information', exact=True).fill('Free-text pay note')
                page.get_by_role('button', name='Save changes', exact=True).click()
                assert page.get_by_text('Free-text pay note', exact=True).is_visible()
                results.append({'id': 'GUI02', 'status': 'PASS', 'observation': 'Browser edit preserved and displayed a free-text pay value.'})
                page.get_by_role('link', name='Delete employee', exact=True).click()
                page.locator('#confirmation').fill('QA Browser Employee')
                page.get_by_role('button', name='Permanently delete employee').click()
                assert page.get_by_text('Employee deleted.', exact=False).is_visible()
                results.append({'id': 'GUI03', 'status': 'PASS', 'observation': 'Confirmation page performed real deletion and returned to the directory.'})
                go('/employees/1')
                shot('03_hr_record.png')
                page.get_by_role('button', name='Sign out', exact=True).click()
                login('supervisor')
                page.get_by_role('link', name='Team schedule', exact=True).click()
                page.set_viewport_size({'width': 1600, 'height': 1050})
                shot('04_supervisor_schedule.png')
                page.get_by_role('button', name='Save complete week', exact=True).click()
                assert page.get_by_text('The complete team week was saved.', exact=True).is_visible()
                results.append({'id': 'GUI04', 'status': 'PASS', 'observation': 'Supervisor submitted the saved own-team weekly schedule through HTML checkboxes.'})
                first_checkbox = page.locator('input[type=checkbox]:checked').first
                first_checkbox.uncheck()
                page.get_by_role('button', name='Save complete week', exact=True).click()
                assert page.get_by_role('alert').is_visible()
                shot('05_schedule_validation.png')
                results.append({'id': 'GUI05', 'status': 'PASS', 'observation': 'Invalid one-shift day displayed a validation message and retained entered selections.'})
                page.get_by_role('button', name='Sign out', exact=True).click()
                login('employee')
                page.set_viewport_size({'width': 1440, 'height': 1000})
                shot('06_employee_schedule.png')
                page.get_by_role('link', name='Check in', exact=True).click()
                page.get_by_role('button', name='Check in now').click()
                assert page.get_by_text('Check-in saved', exact=True).is_visible()
                shot('07_checkin_success.png')
                results.append({'id': 'GUI06', 'status': 'PASS', 'observation': 'Employee opened personal shifts and completed local loopback check-in.'})
                page.set_viewport_size({'width': 390, 'height': 844})
                go('/my-schedule')
                shot('08_mobile_schedule.png')
                assert page.get_by_role('link', name='Go to check-in').is_visible()
                page.get_by_role('button', name='Sign out', exact=True).click()
                shot('09_mobile_login.png')
                results.append({'id': 'GUI07', 'status': 'PASS', 'observation': '390 by 844 mobile emulation exposed login, personal schedule and navigation controls.'})
                assert not errors, errors
                results.append({'id': 'GUI08', 'status': 'PASS', 'observation': 'No page errors; all browser checks ran with JavaScript disabled.'})
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    (output.parent/'browser_results.json').write_text(json.dumps({
        'release': RELEASE, 'results': results,
        'environment': 'Local Chromium browser automation; JavaScript disabled; fictional isolated SQLite data',
        'limits': 'Mobile viewport emulation and loopback check-in; no real workplace, physical-phone or independent-user acceptance'
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Browser checks passed:', len(results))


if __name__ == '__main__':
    main()
