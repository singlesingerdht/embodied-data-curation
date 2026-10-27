"""Validate a clean checkout; optional official environment is supplied explicitly."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile

p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
p.add_argument('--official-python')
p.add_argument('--source-test-log', type=Path)
args = p.parse_args()
repo = Path(__file__).resolve().parents[1]
e = args.output.resolve()
e.mkdir(parents=True, exist_ok=True)
work = Path(tempfile.mkdtemp(prefix='edc-canonical-package-'))
env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}

def call(command, log, cwd=work, extra=None):
    with (e/log).open('w') as f:
        result = subprocess.run(command, cwd=cwd, env={**env, **(extra or {})},
                                stdout=f, stderr=subprocess.STDOUT)
    if result.returncode:
        print((e/log).read_text()[-6000:], flush=True)
        raise SystemExit(result.returncode)

if args.source_test_log:
    (e/'source-tests.log').write_text(args.source_test_log.read_text())
else:
    call([sys.executable, '-m', 'pytest', 'tests/edc', '-q', '-rs'], 'source-tests.log', repo,
         {'PYTHONPATH': str(repo/'src'), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
call([sys.executable, '-m', 'build', '--no-isolation', '--outdir', str(work/'dist')], 'build.log', repo)
wheel = next((work/'dist').glob('*.whl'))
sdist = next((work/'dist').glob('*.tar.gz'))
with zipfile.ZipFile(wheel) as z:
    for file in ['edc/data/ir/canonical.py', 'edc/data/transforms/orientation.py', 'edc/data/channel_registry.yaml']:
        assert z.read(file) == (repo/'src'/file).read_bytes()
with tarfile.open(sdist) as archive:
    names = archive.getnames()
    for required in ['configs/hand_gripper_tactile.yaml', 'configs/imu.yaml',
                     'configs/odometry.yaml', 'tests/edc/test_canonical_orientation.py',
                     'docs/framework/ORIENTATION_HAND_TACTILE.md']:
        assert any(n.endswith('/'+required) for n in names), required
    assert not any('/plugins/dayan/' in n or '/configs/validation/' in n or
                   '/docs/validation/x1_pro_b_' in n for n in names)
    archive.extractall(work/'sdist', filter='data')
source = next((work/'sdist').iterdir())
call([sys.executable, '-m', 'pip', 'install', '--no-deps', '--target', str(work/'installed'), str(wheel)],
     'wheel-install.log')
call([sys.executable, '-m', 'venv', str(work/'base-env')], 'base-venv.log')
base = str(work/'base-env/bin/python')
call([base, '-m', 'pip', 'install', str(wheel)], 'base-install.log')
call([base, '-m', 'pip', 'check'], 'base-pip-check.log')
check = '''import json, sys, tempfile, importlib.metadata as m
from pathlib import Path
import numpy as np
import edc
from edc.algorithms.registry import algorithms
path = Path(tempfile.mkdtemp())
(path/'trajectory.json').write_text(json.dumps({'data': [{'timestamp': 1, 'attitude': [0, 0, 90]}]}))
profile = {'schema_version': 1, 'id': 'synthetic_install_check', 'version': '1', 'channels': {
    'attitude': {'modality': 'pose', 'fields': ['orientation'], 'representation': {
        'kind': 'rpy', 'axes': 'xyz', 'convention': 'extrinsic', 'unit': 'deg'}, 'unit': 'deg'}}}
with edc.open_dataset(path, profile=profile) as ds, next(ds.iter_episodes()).opened() as ep:
    q = ep.read_signal('attitude', 'orientation')
    np.testing.assert_allclose(q, [[0, 0, np.sqrt(.5), np.sqrt(.5)]])
    assert ep.orientation_evidence()['fields'][0]['normalization_applied']
    np.testing.assert_array_equal(ep.inspect_recorded_signal('attitude', 'orientation'), [[0, 0, 90]])
assert len(edc.load_channel_registry()['channels']) == 107
assert len(algorithms().plugins) == 9
packages = sorted(d.metadata['Name'] for d in m.distributions())
assert not any(n.lower().startswith(('torch', 'nvidia', 'lerobot', 'scipy')) for n in packages)
print(json.dumps({'version': edc.__version__, 'standard_channels': 107, 'algorithms': 9,
    'packages': packages, 'quaternion': q.tolist(), 'torch_imported': 'torch' in sys.modules}))
'''
call([base, '-c', check], 'base-check.json')
print('Build and clean NumPy/PyYAML installation passed', flush=True)
installed_env = {'PYTHONPATH': str(work/'installed'), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
call([sys.executable, '-m', 'pytest', 'tests/edc', '-q', '-rs'], 'wheel-tests.log', source, installed_env)
print('Installed wheel full regression passed', flush=True)
if args.official_python:
    official = ['test_canonical_orientation.py', 'test_orientation_channels.py', 'test_lerobot_official.py',
                'test_logical_channel_names.py', 'test_video_native_inputs.py', 'test_media_reuse.py']
    call([args.official_python, '-m', 'pytest', *['tests/edc/'+f for f in official], '-q', '-rs'],
         'official-tests.log', source, installed_env)
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
result = {'wheel': wheel.name, 'sdist': sdist.name, 'wheel_sha256': digest(wheel),
    'sdist_sha256': digest(sdist), 'source_tests': (e/'source-tests.log').read_text().splitlines()[-1],
    'wheel_tests': (e/'wheel-tests.log').read_text().splitlines()[-1],
    'official_tests': (e/'official-tests.log').read_text().splitlines()[-1] if args.official_python else 'not run',
    'base_install': json.loads((e/'base-check.json').read_text())}
(e/'package.json').write_text(json.dumps(result, indent=2)+chr(10))
print(json.dumps({'workspace': str(work), **result}), flush=True)
