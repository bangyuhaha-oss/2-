"""只读检查浏览器已有打印PDF并渲染供视觉复核；pypdf/Poppler只为开发QA。"""
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    browser = json.loads((ROOT / 'evidence/website_enhancement_browser.json').read_text(encoding='utf-8'))
    report = {'release': browser['release'], 'browser_run': browser['run_id'],
              'recorded_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'), 'files': []}
    for item in browser['print_pdfs']:
        path = ROOT / item['path']
        assert sha(path) == item['sha256']
        reader = PdfReader(path)
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        assert reader.pages and text.strip()
        output = path.parent / 'print_render'
        output.mkdir(exist_ok=True)
        prefix = output / (path.stem + '-page')
        subprocess.run(['pdftoppm', '-png', '-scale-to', '1600', str(path), str(prefix)], check=True, capture_output=True)
        images = sorted(output.glob(path.stem + '-page-*.png'))
        assert len(images) == len(reader.pages)
        record = {'pdf': item['path'], 'sha256': item['sha256'], 'pages': len(reader.pages),
                  'nonempty_text': True, 'landscape_pages': all(float(page.mediabox.width) > float(page.mediabox.height) for page in reader.pages),
                  'rendered': [{'path': image.relative_to(ROOT).as_posix(), 'sha256': sha(image)} for image in images]}
        if 'history' in path.stem:
            record['own_record_labels'] = text.count('Employee record #1')
            record['saved_result_labels'] = text.count('Check-in saved')
            assert record['own_record_labels'] == 26 and record['saved_result_labels'] == 26
        report['files'].append(record)
    report['structural_passed'] = True
    report['visual_review'] = 'Pending inspection of rendered pages; structural success alone is not visual acceptance.'
    report['boundary'] = 'Headless browser print output and Poppler rendering; not physical printer, independent user or teacher acceptance.'
    (ROOT / 'evidence/website_enhancement_print_review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
