"""Run only inside the selected worker. Import checks never load model weights."""
import hashlib
import importlib
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import shutil
import sys


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_openpi():
    if metadata.version('transformers') != '4.53.2':
        raise ValueError('OpenPI replacement requires Transformers 4.53.2')
    source = Path(importlib.util.find_spec('openpi').origin).parent / 'models_pytorch/transformers_replace'
    target = Path(importlib.util.find_spec('transformers').origin).parent
    paths = sorted(source.rglob('*.py'))
    if not paths or not (source / 'models/siglip/modeling_siglip.py').is_file():
        raise ValueError('Supplied OpenPI wheel lacks its required Transformers replacement')
    evidence = []
    for path in paths:
        dest = target / path.relative_to(source)
        if not dest.parent.is_dir():
            raise ValueError(f'Replacement does not match installed Transformers: {dest}')
        evidence.append({'file': path.relative_to(source).as_posix(),
                         'original_sha256': _hash(dest) if dest.is_file() else None, 'replacement_sha256': _hash(path)})
    for path in paths:
        shutil.copyfile(path, target / path.relative_to(source))
    return evidence


def snapshot():
    root = Path(importlib.util.find_spec('edc').origin).parent
    cfg = Path(sys.prefix) / 'pyvenv.cfg'
    isolated = (sys.prefix != sys.base_prefix and cfg.is_file() and
                'include-system-site-packages = false' in cfg.read_text().lower())
    result = {'prefix': sys.prefix, 'isolated': isolated, 'python': sys.version,
              'versions': {d.metadata['Name'].lower().replace('_', '-'): d.version for d in metadata.distributions()},
              'edc_sources': {p.relative_to(root).as_posix(): _hash(p) for p in sorted(root.rglob('*'))
                              if p.is_file() and p.suffix in {'.py', '.json', '.yaml'}}}
    if importlib.util.find_spec('openpi'):
        source = Path(importlib.util.find_spec('openpi').origin).parent / 'models_pytorch/transformers_replace'
        target = Path(importlib.util.find_spec('transformers').origin).parent
        result['transformers_replacement'] = {
            p.relative_to(source).as_posix(): _hash(target / p.relative_to(source)) for p in sorted(source.rglob('*.py'))}
    return result


def imports(names):
    for name in names:
        importlib.import_module(name)
    if 'openpi.models_pytorch.pi0_pytorch' in names:
        from transformers.models.siglip import check
        if not check.check_whether_transformers_replace_is_installed_correctly():
            raise ValueError('OpenPI Transformers replacement check failed')
    return {'imports': names, 'model_inference_checked': False}


if __name__ == '__main__':
    action = sys.argv[1]
    result = snapshot() if action == 'snapshot' else patch_openpi() if action == 'patch' else imports(sys.argv[2:])
    print(json.dumps(result))
