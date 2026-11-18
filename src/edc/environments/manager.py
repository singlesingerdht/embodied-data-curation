"""Create isolated workers from named recipes and retain resolved install evidence.

Recipes pin the coupled numerical/model stack. pip resolves remaining packages
once; its report, freeze and all installed versions belong to that environment.
Nothing is installed in the calling interpreter or an existing environment.
"""
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def profiles():
    return json.loads(files(__package__).joinpath('profiles.json').read_text())['profiles']


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _env():
    # Neither Python import paths nor pip destination overrides may escape the venv.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'PIP_'))}
    # Keep explicitly configured package mirrors/certificates, never install targets.
    for key in ('PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_TRUSTED_HOST', 'PIP_CERT',
                'PIP_CLIENT_CERT', 'PIP_CACHE_DIR', 'PIP_TIMEOUT', 'PIP_NO_INDEX', 'PIP_FIND_LINKS'):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(PYTHONNOUSERSITE='1', PIP_CONFIG_FILE=os.devnull)
    return env


def _run(command, *, log=None):
    from contextlib import nullcontext
    output = []
    with Path(log).open('a') if log else nullcontext() as out:
        with subprocess.Popen([str(x) for x in command], cwd='/', env=_env(),
                              text=True, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            for line in process.stdout:
                output.append(line)
                if out:
                    out.write(line)
                    out.flush()
            status = process.wait()
    result = ''.join(output)
    if status:
        raise RuntimeError(f'Environment command failed ({status}); '
                           f'{"see " + str(log) if log else result[-2000:]}')
    return result


def plan(profile, components):
    recipe = profiles()[profile]
    selected = sorted(set(components))
    if not selected:
        raise ValueError('Select at least one --with component')
    unknown = set(selected) - recipe['components'].keys()
    if unknown:
        raise ValueError(f'{profile} does not support components: {sorted(unknown)}')
    entries = [recipe['components'][name] for name in selected]
    return {'schema_version': 1, 'profile': profile, 'components': selected,
            'python_version': recipe['python'], 'platform': recipe['platform'],
            'requirements': sorted(set(recipe['requirements'] + [r for e in entries for r in e['requirements']])),
            'sources_required': sorted({s for e in entries for s in e['sources']}),
            'imports': sorted({'torch', 'torchvision', 'transformers'} | {s for e in entries for s in e['imports']}),
            'algorithms': [a for e in entries for a in e['algorithms']],
            'transformers_patch': recipe.get('transformers_patch', False),
            'limitations': [recipe['description']] + [e['limitation'] for e in entries if 'limitation' in e]}


def _sources(values, required):
    sources = {}
    for value in values:
        name, sep, path = value.partition('=')
        if not sep or name in sources or name not in required:
            raise ValueError('Use one --source NAME=/absolute/path/package.whl per required source')
        wheel = Path(path).expanduser().resolve()
        if not wheel.is_file() or wheel.suffix != '.whl':
            raise ValueError(f'{name}: provide a built local wheel, not a mutable checkout')
        sources[name] = {'path': str(wheel), 'sha256': _sha(wheel)}
    if set(sources) != set(required):
        raise ValueError(f'Missing local source wheels: {sorted(set(required) - sources.keys())}')
    return sources


def _verify_edc_wheel(wheel):
    import edc
    root = Path(edc.__file__).parent
    expected = {p.relative_to(root).as_posix(): _sha(p) for p in root.rglob('*')
                if p.is_file() and p.suffix in {'.py', '.json', '.yaml'}}
    with zipfile.ZipFile(wheel) as archive:
        actual = {name[4:]: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                  if name.startswith('edc/') and Path(name).suffix in {'.py', '.json', '.yaml'}}
    if actual != expected:
        raise ValueError('EDC wheel source differs from the calling framework; build/install the same wheel first')


def create(profile, components, *, target, python, edc_wheel, sources=()):
    data = plan(profile, components)
    target = Path(target).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise ValueError('Environment destination must not exist; existing environments are never modified')
    wheel = Path(edc_wheel).expanduser().resolve()
    if not wheel.is_file() or wheel.suffix != '.whl' or not wheel.name.startswith('embodied_data_curation-'):
        raise ValueError('Provide the EDC wheel used by the calling framework')
    _verify_edc_wheel(wheel)
    data['sources'] = _sources(sources, data['sources_required'])
    info = json.loads(_run([python, '-c', "import sys,platform,json; print(json.dumps({'python':f'{sys.version_info.major}.{sys.version_info.minor}','platform':sys.platform+'-'+platform.machine()}))"]))
    if info != {'python': data['python_version'], 'platform': data['platform']}:
        raise ValueError(f'Profile requires Python {data["python_version"]} on {data["platform"]}; got {info}')
    data['edc_wheel'] = {'path': str(wheel), 'sha256': _sha(wheel)}
    # An incomplete installation remains inspectable and cannot be reused silently.
    target.mkdir(parents=True)
    evidence = target / 'edc-environment'
    evidence.mkdir()
    manifest = evidence / 'manifest.json'
    data['status'] = 'installing'
    manifest.write_text(json.dumps(data, indent=2))
    worker = target / 'bin/python'
    log = evidence / 'install.log'
    try:
        _run([python, '-m', 'venv', str(target)], log=log)
        _run([worker, '-m', 'pip', 'install', 'pip==25.3'], log=log)
        requirements = evidence / 'requirements.txt'
        requirements.write_text('\n'.join(data['requirements']) + '\n')
        install = [wheel.as_uri()] + [f'{name} @ {Path(item["path"]).as_uri()}' for name, item in data['sources'].items()]
        _run([worker, '-m', 'pip', 'install', '--report', evidence / 'install-report.json',
              '-r', requirements, *install], log=log)
        if data['transformers_patch']:
            data['patches'] = json.loads(_run([worker, '-m', 'edc.environments.probe', 'patch'], log=log))
        data['snapshot'] = json.loads(_run([worker, '-m', 'edc.environments.probe', 'snapshot'], log=log))
        (evidence / 'freeze.txt').write_text(_run([worker, '-m', 'pip', 'freeze', '--all'], log=log))
        data['status'] = 'installed'
        manifest.write_text(json.dumps(data, indent=2))
        report = check(target, imports=True)
        if not report['ok']:
            raise RuntimeError('Installation checks failed: ' + '; '.join(report['errors']))
        data['status'] = 'installation_checked'
    except Exception as exc:
        data['status'] = 'failed'
        data['error'] = str(exc)
        raise
    finally:
        manifest.write_text(json.dumps(data, indent=2))
    return {'python': str(worker), 'manifest': str(manifest), 'status': data['status'],
            'algorithms': data['algorithms'], 'limitations': data['limitations']}


def check(target, *, imports=False):
    target = Path(target).expanduser().absolute()
    manifest = target / 'edc-environment/manifest.json'
    data = json.loads(manifest.read_text())
    worker = target / 'bin/python'
    errors = []
    try:
        info = json.loads(_run([worker, '-m', 'edc.environments.probe', 'snapshot']))
        if not info['isolated'] or Path(info['prefix']).resolve() != target.resolve():
            errors.append('Worker is not an isolated environment at the requested path')
        if info != data.get('snapshot'):
            errors.append('Installed versions, EDC source or patched files changed since installation')
        if data['status'] not in {'installed', 'installation_checked'}:
            errors.append(f'Installation status is {data["status"]}')
        _run([worker, '-m', 'pip', 'check'])
        if imports:
            _run([worker, '-m', 'edc.environments.probe', 'imports', *data['imports']])
    except (RuntimeError, OSError, ValueError) as exc:
        errors.append(str(exc))
    report = {'ok': not errors, 'python': str(worker), 'profile': data['profile'],
              'components': data['components'], 'imports_checked': imports,
              'model_inference_checked': False, 'errors': errors}
    (manifest.parent / 'check.json').write_text(json.dumps(report, indent=2))
    return report
