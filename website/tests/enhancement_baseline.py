"""记录本轮不可改动的数据/正式文档及原2.0包指纹；不读出个人资料。"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def snapshot():
    names = ['website/instance/workforce.sqlite', 'website/instance/secret.key',
             'scrum/board.sqlite', 'scrum/tasks.csv', 'scrum/events.csv', 'scrum/.secret',
             'Meiti_Group2_Final_20261028.zip', 'Meiti_Group2_Final_v2.0.0_20261028.zip',
             'Meiti_Group2_Final_v2.0.0_20261028.zip.sha256']
    names += [path.relative_to(ROOT).as_posix() for area in ('documents', 'presentation')
              for path in (ROOT / area).glob('*') if path.suffix.lower() in {'.docx', '.pptx'}
              and not path.name.startswith('~$')]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in names if (ROOT / name).is_file()}


if __name__ == '__main__':
    output = ROOT / 'evidence/website_enhancement_baseline.json'
    if output.exists():
        previous = json.loads(output.read_text(encoding='utf-8'))
        current = snapshot()
        differences = [name for name, sha in previous['protected_sha256'].items() if current.get(name) != sha]
        result = {'recorded_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
                  'unchanged': not differences, 'differences': differences, 'protected_files': len(current)}
        (ROOT / 'evidence/website_enhancement_protected_check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
        raise SystemExit(1 if differences else 0)
    report = {'recorded_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
              'protected_sha256': snapshot(),
              'boundary': 'No data, contributions, acceptance or approval state is changed by this snapshot.'}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Protected baseline recorded:', len(report['protected_sha256']))
