"""独立浏览器复核：全部业务请求使用临时数据库，页面禁用 JavaScript。

Playwright 仅为本机开发验证工具，不属于网站运行依赖。
DOM 测量通过浏览器自动化接口读取，不向应用注入脚本或事件处理器。
"""
import json
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import RELEASE, create_app


class NoScriptParser(HTMLParser):
    """同时检查渲染后的 HTML，防止模板表达式掩盖危险属性。"""
    def __init__(self):
        super().__init__()
        self.violations = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'script':
            self.violations.append('script element')
        for key, value in attrs:
            key, value = key.lower(), (value or '').strip().lower()
            if key.startswith('on'):
                self.violations.append('event attribute ' + key)
            if key in ('href', 'src', 'action', 'formaction') and re.match(
                    r'(?:(?:java|vb)script\s*:|data\s*:\s*text/html)', value):
                self.violations.append('script-capable URL ' + key)


def main():
    evidence = ROOT.parent / 'evidence'
    screenshots = evidence / 'screenshots'
    screenshots.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    report = {
        'release': RELEASE, 'run_id': run_id, 'status': 'RUNNING',
        'environment': {'python': sys.version.split()[0], 'javascript_enabled': False,
                        'database': 'isolated temporary SQLite; no classroom data changed'},
        'checks': [], 'screenshots': [], 'layouts': [], 'page_errors': [],
        'script_requests': [], 'rendered_no_script_violations': [],
        'limits': ['Local developer browser verification only.',
                   'Mobile viewport emulation, not physical-phone acceptance.',
                   'Loopback/source-policy simulation, not actual store or WAN acceptance.',
                   'No peer, teacher, contribution or final client acceptance is asserted.'],
    }
    server = alternate = None
    threads = []

    def check(identifier, condition, observation):
        report['checks'].append({'id': identifier, 'status': 'PASS' if condition else 'FAIL',
                                 'observation': observation})
        assert condition, identifier + ': ' + observation

    try:
        template_files = sorted((ROOT / 'templates').glob('*.html'))
        forbidden = []
        for path in template_files:
            text = path.read_text(encoding='utf-8')
            if re.search(r'<\s*script\b|\s+on[a-z]+\s*=|(?:java|vb)script\s*:', text, re.I):
                forbidden.append(path.name)
        check('WR01', len(template_files) == 10 and not forbidden,
              f'All {len(template_files)} templates scanned; no script tags, event attributes or script URLs: {forbidden}.')
        check('WR02', not list((ROOT / 'static').rglob('*.js')),
              'No JavaScript file exists under website/static.')

        with tempfile.TemporaryDirectory(prefix='meiti-website-review-') as temporary:
            database = str(Path(temporary) / 'review.sqlite')
            app = create_app({'DATABASE': database, 'TESTING': True,
                              'SECRET_KEY': 'isolated-website-review-key',
                              'ONSITE_NETWORKS': '127.0.0.1/32,::1/128'})
            server = make_server('127.0.0.1', 0, app)
            threads.append(threading.Thread(target=server.serve_forever, daemon=True))
            threads[-1].start()
            base = f'http://127.0.0.1:{server.server_port}'

            with sync_playwright() as playwright:
                candidates = sorted((Path.home() / 'AppData/Local/ms-playwright').glob('chromium-*/chrome-win*/chrome.exe'))
                installed_chrome = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
                executable = str(candidates[-1]) if candidates else str(installed_chrome) if installed_chrome.exists() else None
                browser = playwright.chromium.launch(headless=True, executable_path=executable)
                report['environment']['browser'] = browser.version
                report['environment']['browser_executable'] = executable or 'Playwright default'
                context = browser.new_context(viewport={'width': 1440, 'height': 1000},
                                              java_script_enabled=False, reduced_motion='reduce')
                page = context.new_page()
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                page.on('request', lambda request: report['script_requests'].append(request.url)
                        if request.resource_type == 'script' else None)

                def go(path):
                    response = page.goto(base + path)
                    page.wait_for_load_state('networkidle')
                    parser = NoScriptParser()
                    parser.feed(page.content())
                    if parser.violations:
                        report['rendered_no_script_violations'].append({'path': path, 'violations': parser.violations})
                    csp = response.headers.get('content-security-policy', '')
                    assert "script-src 'none'" in csp, path + ': missing no-script CSP'
                    assert response.headers.get('cache-control') == 'no-store'
                    return response

                def shot(name):
                    filename = f'website_review_{run_id}_{name}.png'
                    page.screenshot(path=str(screenshots / filename), full_page=True)
                    report['screenshots'].append({'name': name, 'file': 'screenshots/' + filename})
                    layout = page.evaluate("""() => ({
                        viewport: innerWidth, documentWidth: document.documentElement.scrollWidth,
                        bodyWidth: document.body.scrollWidth,
                        scrollTables: [...document.querySelectorAll('.table-scroll')].map(e => ({width:e.clientWidth, scrollWidth:e.scrollWidth})),
                        language: document.documentElement.lang
                    })""")
                    report['layouts'].append({'name': name, **layout})
                    assert layout['documentWidth'] <= layout['viewport'] + 1, f'{name}: page overflow {layout}'
                    assert layout['language'] == 'en'

                def login(username):
                    go('/login')
                    page.get_by_label('Username', exact=True).fill(username)
                    page.get_by_label('Password', exact=True).fill('Demo!2026')
                    page.get_by_role('button', name='Sign in to workspace').click()
                    page.wait_for_load_state('networkidle')

                def logout():
                    page.get_by_role('button', name='Sign out', exact=True).click()
                    page.get_by_role('heading', name='Sign in', exact=True).wait_for()

                go('/login')
                shot('01_login_desktop')
                check('WR03', page.locator('nav a').count() == 0,
                      'Anonymous login page has no protected navigation; CSP denies scripts and response is no-store.')
                login('hr')
                check('WR04', page.locator('.metric').all_text_contents() == ['500', '500'],
                      'HR sees actual initial total and matching count of 500 fictional employee records.')
                check('WR05', not page.get_by_role('link', name='Team schedule', exact=True).count()
                      and not page.get_by_role('link', name='My schedule', exact=True).count(),
                      'HR navigation exposes workforce only, with no supervisor or employee functions.')
                shot('02_hr_workforce_desktop')
                go('/employees?name=Alex+Chen')
                check('WR06', page.locator('tbody tr').count() == 2 and 'Record #3' in page.locator('tbody').inner_text()
                      and 'Record #4' in page.locator('tbody').inner_text(),
                      'Same-name employees remain separate, selectable records with contextual identities.')
                go('/employees?name=NoSuchIndependentReviewPerson')
                check('WR07', page.get_by_text('No employees match your search.', exact=False).is_visible(),
                      'Search returns a readable empty state; no fabricated row is shown.')
                shot('03_hr_empty_search')
                page.get_by_role('link', name='Clear', exact=True).click()
                check('WR08', page.locator('.metric').all_text_contents() == ['500', '500'],
                      'Clear resets all criteria and restores the full authorized directory.')
                go('/employees?name=Demo&department=Retail&position=Store+Assistant')
                check('WR09', page.locator('.metric').nth(1).inner_text() == '248',
                      'Combined HR name/department/position criteria give the independently expected 248 matches.')
                page.get_by_role('link', name='+ Add employee', exact=True).click()
                stress_name = 'Independent' + 'W' * 109  # 合法120字符无空格姓名，检查输入边界布局。
                page.get_by_label('Name', exact=False).fill(stress_name)
                page.get_by_label('Age', exact=True).fill('0')
                page.get_by_label('Position', exact=True).fill('Review Associate')
                page.get_by_label('Department', exact=True).fill('Review Department')
                page.get_by_label('Address', exact=True).fill('Fictional review address. ' * 30)
                page.get_by_label('Pay information', exact=True).fill('Fictional free-text wage note')
                page.get_by_label('Emergency contact', exact=True).fill('Fictional independent contact, 000-0000-0000')
                shot('04_hr_add_form')
                page.get_by_role('button', name='Save employee', exact=True).click()
                record_path = page.url.removeprefix(base)
                check('WR10', page.locator('.record-grid dt').count() == 7
                      and page.get_by_text('Fictional free-text wage note', exact=True).is_visible(),
                      'HTML form creates and reopens all seven groups, age zero, free-text pay and a valid 120-character unbroken name.')
                shot('05_hr_detail_long_text')
                page.get_by_role('link', name='Edit employee', exact=True).click()
                page.get_by_label('Pay information', exact=True).fill('Unsaved cancellation example')
                page.get_by_role('link', name='Cancel', exact=True).click()
                check('WR11', page.get_by_text('Fictional free-text wage note', exact=True).is_visible()
                      and not page.get_by_text('Unsaved cancellation example', exact=True).count(),
                      'Cancel leaves saved employee values unchanged.')
                page.get_by_role('link', name='Delete employee', exact=True).click()
                page.get_by_label('Type', exact=False).fill('wrong confirmation')
                page.get_by_role('button', name='Permanently delete employee', exact=True).click()
                check('WR12', page.get_by_role('alert').is_visible(),
                      'Wrong deletion confirmation is rejected; the selected record remains available.')
                shot('06_delete_confirmation_error')
                page.get_by_role('link', name='Cancel', exact=True).click()
                page.get_by_role('link', name='Delete employee', exact=True).click()
                page.get_by_label('Type', exact=False).fill(stress_name)
                page.get_by_role('button', name='Permanently delete employee', exact=True).click()
                check('WR13', page.locator('.metric').all_text_contents() == ['500', '500']
                      and go(record_path).status == 404,
                      'Confirmed deletion removes the live record and restores current total to 500.')
                check('WR14', go('/team-schedule').status == 403 and go('/my-schedule').status == 403,
                      'Direct HR attempts to open supervisor scheduling and employee schedule are refused.')
                logout()

                login('supervisor')
                check('WR15', page.locator('.metric').first.inner_text() == '3'
                      and not page.get_by_role('link', name='+ Add employee', exact=True).count(),
                      'Supervisor sees only the configured three-person team and no HR creation control.')
                check('WR16', go('/employees/1').status == 403 and 'Fictional address' not in page.content(),
                      'Supervisor full-record request is denied without private employee values.')
                check('WR17', go('/team-schedule?team_id=2').status == 403,
                      'A direct foreign-team schedule reference is refused.')
                go('/team-schedule')
                shot('07_team_schedule_desktop')
                check('WR18', page.locator('input[type=checkbox]:checked').count() == 30,
                      'Team HTML grid contains the saved 3 × 5 × 2 manual fixture assignments.')
                page.locator('input[type=checkbox]:checked').first.uncheck()
                page.get_by_role('button', name='Save complete week', exact=True).click()
                check('WR19', page.get_by_role('alert').is_visible()
                      and page.locator('input[type=checkbox]:checked').count() == 29,
                      'Invalid complete-week submission gives an explicit error and retains incomplete selections for correction.')
                shot('08_team_schedule_validation')
                page.get_by_role('link', name='Discard unsaved changes', exact=True).click()
                check('WR20', page.locator('input[type=checkbox]:checked').count() == 30,
                      'Discard reloads the previously saved complete week.')
                page.get_by_role('button', name='Save complete week', exact=True).click()
                check('WR21', page.get_by_text('The complete team week was saved.', exact=True).is_visible(),
                      'Valid whole-team save completes through a server-rendered POST/redirect/GET flow.')
                page.set_viewport_size({'width': 390, 'height': 844})
                shot('09_team_schedule_mobile_internal_scroll')
                check('WR22', page.locator('.table-scroll').evaluate('(e) => e.scrollWidth > e.clientWidth'),
                      'Narrow supervisor grid scrolls within its table container without widening the viewport.')
                logout()

                login('employee')
                check('WR23', page.locator('.day-row').count() == 7 and page.locator('input[type=checkbox]').count() == 0,
                      'Employee sees seven days of own saved assignments without editing controls.')
                check('WR24', not page.get_by_role('link', name='Workforce', exact=True).count()
                      and not page.get_by_role('link', name='Team schedule', exact=True).count(),
                      'Employee navigation exposes personal schedule/check-in only.')
                shot('10_personal_schedule_mobile')
                check('WR25', go('/my-schedule?employee_id=2').status == 403
                      and go('/employees').status == 403 and go('/team-schedule').status == 403,
                      'Direct cross-employee, workforce and supervisor URLs are refused.')
                go('/my-schedule?week=2030-01-07')
                check('WR26', page.get_by_role('heading', name='No schedule for this week', exact=True).is_visible(),
                      'Selected unsaved week has a clear empty state and next action.')
                shot('11_personal_empty_week_mobile')
                go('/check-in')
                shot('12_checkin_mobile_before')
                page.get_by_role('button', name='Check in now', exact=False).click()
                check('WR27', page.locator('.notice.success').is_visible()
                      and '+08:00' in page.locator('.history-row').inner_text(),
                      'Local loopback submission produces explicit success and saved China Standard Time.')
                shot('13_checkin_mobile_success')
                page.set_viewport_size({'width': 1440, 'height': 1000})
                go('/my-schedule')
                shot('14_personal_schedule_desktop')
                go('/check-in')
                shot('15_checkin_desktop')
                page.set_viewport_size({'width': 320, 'height': 740})
                go('/my-schedule')
                shot('16_personal_schedule_320')
                logout()
                shot('17_login_320')

                # 第二个本机服务器模拟不匹配来源策略；不冒称真实远程网络。
                refused_app = create_app({'DATABASE': database, 'TESTING': True,
                                          'SECRET_KEY': 'isolated-website-review-key',
                                          'ONSITE_NETWORKS': '192.0.2.0/24'})
                alternate = make_server('127.0.0.1', 0, refused_app)
                threads.append(threading.Thread(target=alternate.serve_forever, daemon=True))
                threads[-1].start()
                base = f'http://127.0.0.1:{alternate.server_port}'
                page.set_viewport_size({'width': 390, 'height': 844})
                login('employee')
                check('WR28', page.locator('.day-row').count() == 7,
                      'Personal schedule still renders when the configured network does not permit check-in.')
                go('/check-in')
                check('WR29', page.get_by_role('button', name='Check in now', exact=False).is_disabled()
                      and page.locator('.status-label.unavailable').is_visible(),
                      'Unmatched-source policy visibly disables attendance in the interface.')
                shot('18_checkin_policy_refused_mobile')
                check('WR30', not report['script_requests'] and not report['page_errors']
                      and not report['rendered_no_script_violations'],
                      'No application script request, page exception or rendered script/event/URL violation was observed.')
                browser.close()
        report['status'] = 'PASS'
    except Exception as error:
        report['status'] = 'FAIL'
        report['failure'] = f'{type(error).__name__}: {error}'
        if not any(row['status'] == 'FAIL' for row in report['checks']):
            report['checks'].append({'id': 'WR_REVIEW_FAILURE', 'status': 'FAIL',
                                     'observation': report['failure']})
        raise
    finally:
        if alternate:
            alternate.shutdown()
            alternate.server_close()
        if server:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
        report['summary'] = {'pass': sum(row['status'] == 'PASS' for row in report['checks']),
                             'fail': sum(row['status'] == 'FAIL' for row in report['checks']),
                             'screenshots': len(report['screenshots'])}
        (evidence / 'website_visual_results.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], **report['summary'],
                          'report': str(evidence / 'website_visual_results.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
