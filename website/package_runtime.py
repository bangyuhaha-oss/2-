"""生成独立2.1运行包/函数索引，只选实际2.1证据；不覆盖2.0包或课程整包。"""
import ast
import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'evidence'
EXCLUDED_DIRS = {'.venv', 'instance', '__pycache__', '.git', '.browser-qa21'}
EXCLUDED_NAMES = {'.secret', 'secret.key'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release():
    """从常量读取版本，不导入或初始化应用。"""
    tree = ast.parse((ROOT / 'website/app.py').read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'RELEASE' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing release constant')


def allowed(path):
    relative = path.relative_to(ROOT)
    return (not any(part in EXCLUDED_DIRS or part.startswith('.install-check') for part in relative.parts)
            and path.name not in EXCLUDED_NAMES
            and path.suffix.lower() not in {'.pyc', '.sqlite', '.sqlite3', '.db', '.key', '.tmp'}
            and not path.name.endswith(('-wal', '-shm')))


def source_index(paths):
    """当前函数/类起始行来自AST，含workspace新增查询helper。"""
    records = []
    for path in paths:
        if path.suffix == '.py':
            tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
            definitions = [{'name': node.name, 'line': node.lineno,
                            'kind': 'class' if isinstance(node, ast.ClassDef) else 'function'}
                           for node in ast.walk(tree)
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            records.append({'file': path.relative_to(ROOT).as_posix(),
                            'sha256': sha(path), 'definitions': sorted(definitions, key=lambda row: row['line'])})
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--index-only', action='store_true', help='Refresh current AST code index without building an archive')
    options = parser.parse_args()
    version = release()
    assert version == '2.1.0-flask', 'This iteration packager is for the2.1 release only.'
    paths = sorted(path for area in ('website', 'scrum') for path in (ROOT / area).rglob('*')
                   if path.is_file() and allowed(path))
    for path in paths:
        if path.suffix == '.html' and re.search(r'<\s*script\b|\bon\w+\s*=|javascript\s*:',
                                               path.read_text(encoding='utf-8-sig'), re.I):
            raise ValueError('Unexpected application script: ' + str(path))
    assert not any(path.suffix.lower() in {'.js', '.mjs', '.ts'} for path in paths)
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    index_path = OUTPUT / 'website_code_index.json'
    records = source_index(paths)
    existing = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    if existing.get('release') != version or existing.get('files') != records:
        index_path.write_text(json.dumps({'release': version, 'recorded_at': stamp, 'files': records},
                                         ensure_ascii=False, indent=2), encoding='utf-8')
    if options.index_only:
        print(json.dumps({'release': version, 'code_index': index_path.name, 'sha256': sha(index_path)}, indent=2))
        return
    required = ['automated_results.json', 'website_security_results.json', 'website_enhancement_results.json',
                'website_enhancement_browser.json', 'website_scrum_results.json',
                'website_enhancement_resume_audit.json', 'website_enhancement_resume_before.json',
                'website_code_index.json', 'website_enhancement_print_review.json', 'website_enhancement_visual_review.md']
    for name in required:
        path = OUTPUT / name
        assert path.is_file(), 'Required evidence missing: ' + name
        paths.append(path)
    for name in ['website_enhancement_running_state.json', 'website_enhancement_resume_after.json',
                 'website_enhancement_visual_review.md']:
        if (OUTPUT / name).is_file():
            paths.append(OUTPUT / name)
    # 只打包最终成功run列出的截图，不把预览/失败/2.0截图当当前证据。
    browser = json.loads((OUTPUT / 'website_enhancement_browser.json').read_text(encoding='utf-8'))
    assert browser['release'] == version and browser['status'] == 'PASS'
    for filename, fingerprint in browser['source_sha256'].items():
        assert sha(ROOT / 'website' / filename) == fingerprint, 'Browser source drift: ' + filename
    for screenshot in browser['screenshots']:
        path = ROOT / screenshot['path']
        assert path.is_file() and path.resolve().is_relative_to(ROOT)
        assert sha(path) == screenshot['sha256']
        paths.append(path)
    for item in browser.get('print_pdfs', []):
        path = ROOT / item['path']
        assert path.is_file() and sha(path) == item['sha256']
        paths.append(path)
    print_review = json.loads((OUTPUT / 'website_enhancement_print_review.json').read_text(encoding='utf-8'))
    assert print_review['browser_run'] == browser['run_id'] and print_review['structural_passed']
    for item in print_review['files']:
        for rendered in item['rendered']:
            path = ROOT / rendered['path']
            assert path.is_file() and sha(path) == rendered['sha256']
            paths.append(path)
    for relative in ['coordination/website_enhancement_contract.md', 'coordination/website_enhancement_map.md']:
        assert (ROOT / relative).is_file(), relative
        paths.append(ROOT / relative)
    paths = sorted(set(paths))
    manifest = {'release': version, 'recorded_at': stamp,
                'scope': 'Website and unchanged internal Scrum runtime only; course documents and PPT are synchronized separately.',
                'supported_runtime': 'Windows x64, CPython3.12 x64, seven locked offline wheels',
                'javascript_runtime_files': 0, 'template_scan': 'passed',
                'excluded': ['.venv', '.install-check*', 'instance/', 'work databases', 'secrets', 'caches',
                             'old/preview/failed screenshots', 'course documents and presentation'],
                'files': [{'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size,
                           'sha256': sha(path)} for path in paths]}
    manifest_path = OUTPUT / 'website_runtime_manifest_2.1.0.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = OUTPUT / 'website_runtime_bundle_2.1.0.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in [*paths, manifest_path]:
            package.write(path, 'Meiti_Group2/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        for item in manifest['files']:
            assert hashlib.sha256(package.read('Meiti_Group2/' + item['path'])).hexdigest() == item['sha256']
        assert all(allowed(ROOT / name.removeprefix('Meiti_Group2/')) for name in package.namelist())
    receipt = {'release': version, 'recorded_at': stamp, 'archive': archive.name,
               'bytes': archive.stat().st_size, 'file_count': len(paths) + 1, 'sha256': sha(archive),
               'manifest_sha256': sha(manifest_path), 'integrity': 'passed', 'all_member_hashes': 'passed',
               'old2.0_and_course_archives_overwritten': False,
               'boundary': 'Local runtime package only; no Python installer, clean-OS/store/teacher acceptance claim.'}
    (OUTPUT / 'website_package_results_2.1.0.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
