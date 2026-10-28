"""无 JavaScript 浏览器检查；仅写临时看板，不改变真实任务和事件。"""
import json
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from board import ROOT, create_app
from test_board import file_hash


def main():
    protected = [ROOT / name for name in ('board.sqlite', 'tasks.csv', 'events.csv', '.secret')]
    before = {path.name: file_hash(path) for path in protected}
    report = {'timestamp': datetime.now().astimezone().isoformat(), 'javascript_enabled': False,
              'checks': [], 'screenshots': [], 'page_errors': [], 'layouts': [],
              'boundary': 'Copied task/event snapshots and a disposable database only. This is simulated local QA, not real Scrum history or personal contribution.'}
    output = ROOT.parent / 'evidence' / 'screenshots'; output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='group2-board-browser-') as temporary:
        scratch = Path(temporary)
        for name in ('tasks.csv', 'events.csv'):
            shutil.copyfile(ROOT / name, scratch / name)
        config = {'DATABASE': str(scratch / 'browser.sqlite'), 'DELIVERY_ROOT': ROOT.parent,
                  'IMPORT_DIR': scratch, 'EXPORT_DIR': scratch, 'SECRET_KEY': 'isolated-browser-key', 'TESTING': True}
        app = create_app(config)
        server = make_server('127.0.0.1', 0, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome', headless=True)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, java_script_enabled=False)
                page = context.new_page(); page.on('pageerror', lambda error: report['page_errors'].append(str(error)))

                def capture(name):
                    filename = f'scrum_{name}.png'
                    page.screenshot(path=str(output / filename), full_page=True)
                    report['screenshots'].append(f'evidence/screenshots/{filename}')
                    size = page.evaluate('({viewport:innerWidth,document:document.documentElement.scrollWidth})')
                    assert size['viewport'] == size['document'], size
                    report['layouts'].append({'screen': name, **size})

                assert page.goto(base).status == 200
                assert page.locator('script').count() == 0
                capture('desktop')
                card = page.locator('#task-B01')
                card.locator('summary').click()
                card.locator('select[name="status"]').select_option('Review / Test')
                card.locator('select[name="owner"]').select_option('李远向')
                card.locator('input[name="operator"]').fill('Isolated browser QA')
                card.locator('textarea[name="note"]').fill('Browser-only update in a disposable database.')
                card.get_by_role('button', name='Save & record operation').click()
                assert page.get_by_text('Change saved. The operation log and CSV snapshots were updated.', exact=True).is_visible()
                with closing(sqlite3.connect(config['DATABASE'])) as db:
                    event = db.execute('SELECT actor,new_owner FROM events ORDER BY id DESC LIMIT 1').fetchone()
                assert event == ('Isolated browser QA', '李远向')
                report['checks'].append('No-JavaScript form update persists and separates operator from proposed owner')
                card = page.locator('#task-B01'); card.locator('summary').click()
                card.locator('select[name="status"]').select_option('Done')
                card.locator('input[name="evidence"]').fill('all tests passed')
                card.locator('input[name="operator"]').fill('Isolated browser QA')
                card.locator('textarea[name="note"]').fill('Deliberate invalid evidence in isolated QA.')
                card.get_by_role('button', name='Save & record operation').click()
                assert page.get_by_role('heading', name='Request not saved').is_visible()
                capture('evidence_rejected')
                report['checks'].append('Plain-text Done evidence rejected through the real browser form')
                page.get_by_role('link', name='Return to board').click()
                page.set_viewport_size({'width': 390, 'height': 844}); capture('mobile')
                report['checks'].append('Desktop1440 and mobile390 fit the viewport; board/table scroll within their own containers')
                browser.close()
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close()
    report['real_board_files_unchanged'] = before == {path.name: file_hash(path) for path in protected}
    assert report['real_board_files_unchanged'] and not report['page_errors']
    evidence_path = ROOT.parent / 'evidence' / 'website_scrum_results.json'
    report_container = json.loads(evidence_path.read_text(encoding='utf-8')) if evidence_path.exists() else {}
    report_container['browser'] = report
    evidence_path.write_text(json.dumps(report_container, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
