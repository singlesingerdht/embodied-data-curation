"""Reject local plugins and internal work records in framework releases."""
import argparse
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import zipfile


def forbidden_reason(name):
    path = PurePosixPath(name.replace('\\', '/'))
    parts = path.parts
    if any(parts[i:i + 2] == ('plugins', 'value_assessment')
           for i in range(len(parts) - 1)) or any(
            part == 'edc_value' or part.startswith('edc_value_assessment') for part in parts):
        return 'local value plugin'
    if path.name == 'STATUS.md' or any(fnmatchcase(path.name, pattern) for pattern in (
            '*_INTEGRATION_PLAN_*.md', '*_MIGRATION_PLAN_*.md', '*_VALIDATION_*.md')):
        return 'internal work record'
    if any(part in {'internal', 'audits'} for part in parts) or any(
            parts[i:i + 2] == ('docs', 'evidence') for i in range(len(parts) - 1)):
        return 'internal audit material'
    return None


def check_release(root, dist):
    tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                             check=True, capture_output=True, text=True).stdout.split('\0')
    errors = [f'Git: {name} (local value plugin)' for name in tracked
              if name and forbidden_reason(name) == 'local value plugin']
    archives = sorted(dist.glob('embodied_data_curation-*.whl'))
    sdists = sorted(dist.glob('embodied_data_curation-*.tar.gz'))
    if not archives:
        errors.append('Missing framework wheel')
    if not sdists:
        errors.append('Missing framework source distribution')
    archives += sdists
    for archive in archives:
        if archive.suffix == '.whl':
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
        else:
            with tarfile.open(archive, 'r:gz') as package:
                names = package.getnames()
        errors.extend(f'{archive.name}: {name} ({reason})' for name in names
                      if (reason := forbidden_reason(name)))
    return errors, archives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    args = parser.parse_args()
    try:
        errors, archives = check_release(args.root, args.dist)
    except (OSError, subprocess.CalledProcessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'Release content check failed: {exc}\n')
    if errors:
        parser.exit(1, 'Release content check failed:\n' + '\n'.join(errors) + '\n')
    print(f'Release content check passed: Git index and {len(archives)} archives')


if __name__ == '__main__':
    main()
