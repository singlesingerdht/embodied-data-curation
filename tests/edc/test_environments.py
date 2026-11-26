"""Installation boundaries and drift detection, without fetching model packages."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from edc.environments import manager


def test_composable_vision_and_separate_openpi():
    vision = manager.plan('vision', ['initial-state', 'progress', 'progress'])
    assert vision['components'] == ['initial-state', 'progress']
    assert 'transformers==4.57.1' in vision['requirements']
    assert vision['requirements'].count('torch==2.8.0') == 1
    assert vision['sources_required'] == ['sam3']
    assert manager.plan('openpi', ['shapley'])['transformers_patch'] is True
    with pytest.raises(ValueError, match='does not support'):
        manager.plan('vision', ['shapley'])
    with pytest.raises(ValueError, match='at least one'):
        manager.plan('vision', [])


def test_env_plan_is_packaged_and_does_not_import_models():
    code = """from edc.execution.cli import main
import sys
assert main(['env','plan','vision','--with','progress']) == 0
assert not {'torch','transformers','vllm','sam3','openpi'} & sys.modules.keys()
"""
    result = subprocess.run([sys.executable, '-c', code], cwd='/', text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['algorithms'] == ['Progress_Stagnation_Attribution']


def test_refuse_existing_environment_before_any_install(tmp_path, monkeypatch):
    marker = tmp_path / 'keep'; marker.write_text('unchanged')
    monkeypatch.setattr(manager, '_run', lambda *a, **kw: pytest.fail('must not run installer'))
    with pytest.raises(ValueError, match='must not exist'):
        manager.create('vision', ['progress'], target=tmp_path, python='python', edc_wheel='unused')
    assert marker.read_text() == 'unchanged'


def test_source_wheels_are_explicit_complete_and_hashed(tmp_path):
    wheel = tmp_path / 'source.whl'; wheel.write_bytes(b'fixture')
    assert manager._sources([f'sam3={wheel}'], ['sam3'])['sam3']['sha256'] == manager._sha(wheel)
    for sources in ([], [f'sam3={tmp_path}'], [f'sam3={wheel}', f'sam3={wheel}'], [f'unknown={wheel}']):
        with pytest.raises(ValueError):
            manager._sources(sources, ['sam3'])


def test_installer_cannot_inherit_python_or_pip_destination(monkeypatch):
    for key in ['PYTHONPATH', 'PYTHONHOME', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER']:
        monkeypatch.setenv(key, '/unrelated')
    env = manager._env()
    assert not set(['PYTHONPATH','PYTHONHOME','PIP_TARGET','PIP_PREFIX','PIP_USER']) & env.keys()
    assert env['PYTHONNOUSERSITE'] == '1'


def test_failed_install_records_failure_and_never_claims_ready(tmp_path, monkeypatch):
    wheel = tmp_path / 'embodied_data_curation-0.0-py3-none-any.whl'; wheel.write_bytes(b'fixture')
    monkeypatch.setattr(manager, '_verify_edc_wheel', lambda p: None)
    calls = []
    def command(args, **kw):
        calls.append(args)
        if '-c' in args:
            return json.dumps({'python': '3.12', 'platform': 'linux-x86_64'})
        if 'install' in args:
            raise RuntimeError('synthetic dependency conflict')
        return ''
    monkeypatch.setattr(manager, '_run', command)
    target = tmp_path / 'new'
    with pytest.raises(RuntimeError, match='dependency conflict'):
        manager.create('vision', ['progress'], target=target, python='base-python', edc_wheel=wheel)
    data = json.loads((target / 'edc-environment/manifest.json').read_text())
    assert data['status'] == 'failed'
    assert all('--system-site-packages' not in args for args in calls)
    assert calls[-1][0] == target / 'bin/python'


@pytest.mark.parametrize('change', ['none', 'versions', 'edc_sources', 'isolated', 'status'])
def test_check_detects_environment_drift(tmp_path, monkeypatch, change):
    folder = tmp_path / 'edc-environment'; folder.mkdir()
    info = {'prefix': str(tmp_path), 'isolated': True, 'versions': {'torch':'2.8.0'}, 'edc_sources':{'worker.py':'abc'}}
    data = {'profile':'vision','components':['progress'],'imports':['vllm'],
            'status':'installation_checked','snapshot':json.loads(json.dumps(info))}
    if change == 'status': data['status'] = 'failed'
    elif change == 'versions': info['versions']['torch'] = '2.9.0'
    elif change == 'edc_sources': info['edc_sources']['worker.py'] = 'changed'
    elif change == 'isolated': info['isolated'] = False
    (folder / 'manifest.json').write_text(json.dumps(data))
    monkeypatch.setattr(manager, '_run', lambda args: json.dumps(info) if 'snapshot' in args else '')
    report = manager.check(tmp_path, imports=True)
    assert report['ok'] is (change == 'none')
    assert not report['model_inference_checked']


def test_mismatched_edc_wheel_rejected_before_install(tmp_path):
    import zipfile
    wheel = tmp_path / 'embodied_data_curation-0.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('edc/__init__.py', 'different framework')
    with pytest.raises(ValueError, match='source differs'):
        manager.create('vision', ['progress'], target=tmp_path/'new', python='unused', edc_wheel=wheel)
    assert not (tmp_path/'new').exists()


def test_openpi_patches_only_worker_and_preserves_before_after_hashes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from edc.environments import probe
    source = tmp_path/'openpi/models_pytorch/transformers_replace/models/siglip'
    target = tmp_path/'transformers/models/siglip'
    source.mkdir(parents=True); target.mkdir(parents=True)
    (source/'modeling_siglip.py').write_text('replacement')
    (source/'check.py').write_text('new check')
    (target/'modeling_siglip.py').write_text('original')
    monkeypatch.setattr(probe.metadata, 'version', lambda name: '4.53.2')
    monkeypatch.setattr(probe.importlib.util, 'find_spec',
                        lambda name: SimpleNamespace(origin=str(tmp_path/name/'__init__.py')))
    before = manager._sha(target/'modeling_siglip.py')
    evidence = probe.patch_openpi()
    assert (target/'modeling_siglip.py').read_text() == 'replacement'
    assert (target/'check.py').read_text() == 'new check'
    assert {r['file']:r['original_sha256'] for r in evidence} == {
        'models/siglip/modeling_siglip.py':before, 'models/siglip/check.py':None}
    assert (source/'modeling_siglip.py').read_text() == 'replacement'


def test_scene_uses_sam2_source_wheel_without_vllm():
    scene=manager.plan('vision',['scene'])
    assert scene['algorithms']==['Hand_Intervention','Initial_Placement_Assessment']
    assert set(scene['sources_required'])=={'sam2'}
    assert 'transformers==4.57.1' in scene['requirements']
    assert not any(requirement.startswith('vllm') for requirement in scene['requirements'])
    assert {'sam2.build_sam', 'transformers.models.qwen3_vl'} <= set(scene['imports'])