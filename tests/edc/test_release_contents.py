"""Exercise the release gate against tracked files and real archive members."""
import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/check_release_contents.py'
spec = importlib.util.spec_from_file_location('check_release_contents', SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def make_archives(dist, wheel_extra=(), sdist_extra=()):
    dist.mkdir(exist_ok=True)
    with zipfile.ZipFile(dist / 'embodied_data_curation-1.0-py3-none-any.whl', 'w') as archive:
        for name in ['edc/__init__.py', *wheel_extra]:
            archive.writestr(name, b'fixture')
    with tarfile.open(dist / 'embodied_data_curation-1.0.tar.gz', 'w:gz') as archive:
        for name in ['src/edc/__init__.py', 'examples/plugins/algorithm/algorithm.py', *sdist_extra]:
            member = tarfile.TarInfo('embodied_data_curation-1.0/' + name)
            member.size = len(b'fixture')
            archive.addfile(member, io.BytesIO(b'fixture'))


@pytest.fixture
def repo(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    return tmp_path


def test_clean_packages_and_untracked_local_plugin_are_allowed(repo):
    local = repo / 'plugins/value_assessment/algorithm.py'
    local.parent.mkdir(parents=True)
    local.write_text('local only')
    make_archives(repo / 'dist')
    errors, archives = release.check_release(repo, repo / 'dist')
    assert not errors and len(archives) == 2


def test_tracked_value_plugin_is_rejected_even_with_clean_packages(repo):
    local = repo / 'plugins/value_assessment/algorithm.py'
    local.parent.mkdir(parents=True)
    local.write_text('local only')
    subprocess.run(['git', '-C', str(repo), 'add', str(local)], check=True)
    make_archives(repo / 'dist')
    errors, _ = release.check_release(repo, repo / 'dist')
    assert any('Git: plugins/value_assessment/algorithm.py' in error for error in errors)


@pytest.mark.parametrize(('wheel_extra', 'sdist_extra'), [
    (['edc_value/initial_state.py'], []),
    (['edc_value_assessment-1.0.dist-info/METADATA'], []),
    ([], ['plugins/value_assessment/src/edc_value/initial_state.py']),
    ([], ['docs/STATUS.md']),
    ([], ['docs/VALUE_INTEGRATION_PLAN_20261110.md']),
    ([], ['docs/audits/run.json']),
    ([], ['docs/evidence/local-result.json']),
])
def test_forbidden_archive_members_are_rejected(repo, wheel_extra, sdist_extra):
    make_archives(repo / 'dist', wheel_extra, sdist_extra)
    errors, _ = release.check_release(repo, repo / 'dist')
    assert any((wheel_extra or sdist_extra)[0] in error for error in errors)


def test_missing_archives_cannot_pass(repo):
    errors, archives = release.check_release(repo, repo / 'absent')
    assert len(errors) == 2 and not archives
