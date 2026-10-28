"""记录本机2.1入口的HTTP/项目进程与今日保护文件状态，不提交业务表单。"""
import hashlib
import json
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def powershell(command):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        '[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); ' + command],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
    return json.loads(result.stdout)


def main():
    listeners = powershell('Get-NetTCPConnection -State Listen -LocalPort 5000,5001 | Select-Object LocalPort,OwningProcess | ConvertTo-Json')
    processes = powershell('Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json')
    by_id = {item['ProcessId']: item for item in processes}
    checks = []
    for listener in listeners:
        chain = []
        current = by_id[listener['OwningProcess']]
        for _ in range(3):
            chain.append(current)
            current = by_id.get(current['ParentProcessId'])
            if not current:
                break
        assert any(str(ROOT).lower() in ((item['ExecutablePath'] or '') + (item['CommandLine'] or '')).lower() for item in chain)
        url = f'http://127.0.0.1:{listener["LocalPort"]}' + ('/login' if listener['LocalPort'] == 5000 else '/')
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read().decode('utf-8')
            assert response.status == 200
            if listener['LocalPort'] == 5000:
                assert '2.1.0-flask' in body
            checks.append({'url': url, 'http': response.status, 'listener_pid': listener['OwningProcess'], 'project_chain': chain})
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    report = {'release': '2.1.0-flask', 'recorded_at': stamp, 'checks': checks,
              'launch': 'Official website/Start.cmd and scrum/Start_Board.cmd via hidden cmd.exe; stdout/stderr under evidence/website_2.1_*',
              'business_requests': 'GET only; no login/CRUD/schedule/check-in POST to working data',
              'boundary': 'Point-in-time local running state; restart official launchers if services exit, no permanent-service promise.'}
    (ROOT / 'evidence/website_enhancement_running_state.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    before = json.loads((ROOT / 'evidence/website_enhancement_resume_before.json').read_text(encoding='utf-8'))
    actual = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in before['protected_sha256'] if (ROOT / name).is_file()}
    differences = [name for name, expected in before['protected_sha256'].items() if actual.get(name) != expected]
    protected = {'recorded_at': stamp, 'reference': 'website_enhancement_resume_before.json',
                 'protected_sha256': actual, 'differences_since_today_resume': differences,
                 'business_and_scrum_files_unchanged': not any(name.startswith(('website/instance/', 'scrum/')) for name in differences),
                 'old2.0_archives_unchanged': not any(name.endswith(('.zip', '.sha256')) for name in differences),
                 'boundary': 'Historical whole-file difference before today resume remains unexplained; no restoration or overwrite performed.'}
    (ROOT / 'evidence/website_enhancement_resume_after.json').write_text(json.dumps(protected, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'release': report['release'], 'recorded_at': stamp, 'urls': [item['url'] for item in checks],
                      'listener_pids': [item['listener_pid'] for item in checks], 'differences_since_resume': differences}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
