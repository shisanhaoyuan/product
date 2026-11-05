"""Create a portable source + prebuilt frontend release without data or credentials."""
import argparse
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def package(destination):
    if not (ROOT / 'web/dist/index.html').is_file():
        raise SystemExit('先在 frontend 执行 npm ci 和 npm run build')
    if destination.exists():
        raise SystemExit('目标已存在，使用新文件名以保留上一版')
    destination.parent.mkdir(parents=True, exist_ok=True)
    selected = ['catalog', 'web', 'browser-helper', 'frontend/src', 'frontend/public', 'tests', 'deploy',
                'validation/browser_check.py', 'validation/frontend_state_check.py', 'validation/team_browser_check.py',
                'validation/category_browser_check.py', 'validation/capture_helper_check.py',
                'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html',
                'frontend/vite.config.ts', 'frontend/tsconfig.json', 'frontend/.prettierrc.json',
                'README.md', 'MAINTENANCE.md', 'VALIDATION.md', 'pyproject.toml', '.gitignore']
    files = []
    for item in selected:
        path = ROOT / item
        files.extend(path.rglob('*') if path.is_dir() else [path])
    with tarfile.open(destination, 'w:gz') as archive:
        for path in sorted(set(files)):
            if not path.is_file() or path.is_symlink() or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            archive.add(path, arcname=str(Path('jiwu') / path.relative_to(ROOT)), recursive=False)
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    package(parser.parse_args().output.resolve())
