"""Isolated native-method execution and evidence, with no model imports."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from functools import lru_cache

from edc.algorithms.channel_names import resolve_channel
from edc.reporting.results import write_json


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(path):
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ImportError(f'Required local resource not found: {path}')
    members = [path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file())
    if not members:
        raise ValueError(f'Empty resource: {path}')
    return {'path': str(path), 'files': {
        (p.name if path.is_file() else p.relative_to(path).as_posix()): sha256(p) for p in members}}


def environment():
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['PYTHONNOUSERSITE'] = '1'
    for key in ('RANK','LOCAL_RANK','WORLD_SIZE','LOCAL_WORLD_SIZE','MASTER_ADDR','MASTER_PORT'):
        env.pop(key,None)
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', WANDB_MODE='disabled')
    return env


def runtime_info(python, package, dependencies, shared_modules=()):
    # Metadata probe only. Different installation paths are valid when the
    # actual method source bytes match the caller's frozen implementation.
    code = """import hashlib,importlib.metadata as m,importlib.util as u,json,sys
from pathlib import Path
p=u.find_spec(sys.argv[1]); versions={}
for name in sys.argv[3:]:
    try: versions[name]=m.version(name)
    except m.PackageNotFoundError: versions[name]='not-installed'
root=Path(p.origin).parent if p and p.origin else None
sources={x.relative_to(root).as_posix():hashlib.sha256(x.read_bytes()).hexdigest()
         for x in sorted(root.rglob('*')) if x.is_file() and x.suffix in {'.py','.json','.yaml'}} if root else {}
shared=u.find_spec('edc.algorithms.value.runtime')
extra={name:(hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest() if (spec:=u.find_spec(name)) else None) for name in json.loads(sys.argv[2])}
manifest=Path(sys.prefix)/'edc-environment/manifest.json'
profile=({'path':str(manifest),'sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
          'installation':json.loads(manifest.read_text())} if manifest.is_file() else None)
print(json.dumps({'package':None if p is None else p.origin,'versions':versions,'source_files':sources,
                 'shared_runtime_sha256':hashlib.sha256(Path(shared.origin).read_bytes()).hexdigest(),'shared_source_files':extra,
                 'environment_profile':profile}))
"""
    result = subprocess.run([python, '-c', code, package, json.dumps(shared_modules), *dependencies], env=environment(),
                            cwd='/', text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise ImportError('Worker cannot load EDC: ' + result.stderr[-1500:])
    return json.loads(result.stdout)


def check_runtime(params, module_file, modules, *, checks=()):
    root=Path(module_file).resolve().parent
    code="""import importlib,importlib.util as u,json,sys
missing=[]
for name in sys.argv[2:]:
    try: found=u.find_spec(name)
    except ModuleNotFoundError: found=None
    if found is None: missing.append(name)
if not missing:
    for name in json.loads(sys.argv[1]):
        module,attribute=name.rsplit('.',1)
        getattr(importlib.import_module(module),attribute)()
print(json.dumps(missing))
"""
    try:
        result=subprocess.run([params.get('python',sys.executable),'-c',code,json.dumps(checks),*modules],cwd='/',
            env=environment(),text=True,capture_output=True,timeout=60)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise ImportError(f'Worker environment unavailable: {exc}') from exc
    if result.returncode:
        raise ImportError('Worker dependency probe failed: '+result.stderr[-1000:])
    missing=json.loads(result.stdout)
    if missing:
        raise ImportError('Worker dependencies missing: '+', '.join(missing))


def save_provenance(context, params, module_file, models, dependencies, *, shared_modules=()):
    root = Path(module_file).resolve().parent
    info = runtime_info(params.get('python', sys.executable), root_package(root), dependencies, shared_modules) if shared_modules else runtime_info(params.get('python', sys.executable), root_package(root), dependencies)
    import importlib.util
    extra = {name: sha256(importlib.util.find_spec(name).origin) for name in shared_modules}
    if extra and info.get('shared_source_files') != extra:
        raise ImportError('Worker shared input service source fingerprints differ')
    sources={p.relative_to(root).as_posix():sha256(p) for p in sorted(root.rglob('*'))
             if p.is_file() and p.suffix in {'.py','.json','.yaml'}}
    if (not info['package'] or info['source_files'] != sources
            or info['shared_runtime_sha256'] != sha256(__file__)):
        raise ImportError('Worker must install this exact EDC method/runtime; source fingerprints differ')
    data = {'effective_params': params, 'models': {name: fingerprint(path) for name, path in models.items()},
            'source_files': {p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob('*'))
                             if p.is_file() and p.suffix in {'.py', '.json', '.yaml'}},
            'shared_runtime_sha256': sha256(__file__),
            'shared_source_files': extra, 'runtime': info, 'method_source': json.loads((root / 'SOURCE.json').read_text())}
    data['experiment_fingerprint'] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    write_json(Path(context.workdir) / 'provenance.json', data)
    return data


def root_package(root):
    return 'edc.algorithms.value.algorithms.' + root.name


def run_worker(context, params, package, payload):
    work = Path(context.workdir).resolve()
    request = work / 'request.json'
    write_json(request, {'params': params, 'workdir': str(work), **payload})
    with (work / 'worker.log').open('w') as log:
        completed = subprocess.run([params.get('python', sys.executable), '-m', package + '.worker', str(request)],
                                   cwd=work, env=environment(), stdout=log, stderr=subprocess.STDOUT,
                                   timeout=params.get('timeout_seconds', 86400))
    if completed.returncode:
        raise RuntimeError(f'Native worker exited {completed.returncode}; see worker.log')
    return json.loads((work / 'native-result.json').read_text())


@lru_cache(maxsize=16)
def _playback_timeline(path, size, mtime_ns):
    """Read actual presentation times, without changing frames or source clocks."""
    import av
    import numpy as np
    with av.open(path) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate) if stream.average_rate else 0.0
        times = [frame.time for frame in container.decode(stream)]
    if fps <= 0 or len(times) < 2 or any(t is None for t in times):
        raise ValueError('Video requires presentation timestamps and a valid playback FPS')
    t = np.asarray(times, dtype=float)
    t -= t[0]
    if not np.isfinite(t).all() or not np.allclose(t, np.arange(len(t)) / fps, atol=1e-5, rtol=0):
        raise ValueError('Variable playback frame intervals require a separate playback mapping adapter')
    return fps, tuple(t.tolist())


def video_inputs(episode, bindings, *, timing='uniform', max_frame_gap_sec=None):
    """Validate exact mapped frame correspondence; never invent a common clock."""
    import numpy as np
    if not bindings:
        raise ValueError('Explicit RGB view bindings are required')
    if timing not in {'uniform', 'source_mapped'}:
        raise ValueError('Unknown video timing policy')
    resolved = {role: resolve_channel(episode, channel) for role, channel in bindings.items()}
    rows = {}; reference = episode.channels[next(iter(resolved.values()))]
    for role, name in resolved.items():
        c = episode.channels[name]
        if c.modality != 'rgb' or not c.image_available or c.count < 2:
            raise ValueError(f'{name}: RGB frames unavailable')
        if not c.clock or c.time_source not in {'acquisition', 'container'}:
            raise ValueError(f'{name}: actual source time and explicit clock required')
        t = np.asarray(c.timestamps, float)
        if t.ndim != 1 or len(t) != c.count or not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
            raise ValueError(f'{name}: invalid source timestamps')
        clock_mapping = None
        if c.clock != reference.clock:
            transformed = episode.transform_timestamps(name, reference.clock)
            t = np.asarray(transformed.output, dtype=float)
            clock_mapping = getattr(transformed, 'source_mapping', None)
            if t.shape != (c.count,) or not np.isfinite(t).all() or np.any(np.diff(t) <= 0):
                raise ValueError(f'{name}: invalid mapped source timestamps')
        dt = np.diff(t)
        if timing == 'uniform' and not np.allclose(dt, dt[0], atol=1e-5, rtol=1e-4):
            raise ValueError('Native frame-based method requires uniform source intervals; explicit resampling is needed')
        if max_frame_gap_sec is not None:
            if not np.isfinite(max_frame_gap_sec) or max_frame_gap_sec <= 0:
                raise ValueError('max_frame_gap_sec must be finite and positive')
            if np.any(dt > max_frame_gap_sec):
                raise ValueError(f'{name}: source frame gap exceeds max_frame_gap_sec; insufficient evidence across the gap')
        playback = None
        fps = float(1 / dt[0])
        if timing == 'source_mapped':
            path = Path(episode.video_source(name)).resolve()
            stat = path.stat()
            fps, playback = _playback_timeline(str(path), stat.st_size, stat.st_mtime_ns)
            if len(playback) != c.count:
                raise ValueError(f'{name}: playback/source frame count mismatch')
        if rows:
            first = next(iter(rows.values()))
            other = first['mapped_timestamps']
            if len(t) != len(other) or not np.allclose(t, other, atol=1e-5, rtol=0):
                raise ValueError('Views require exact frame correspondence in the mapped clock')
            if playback is not None and not np.allclose(playback, first['playback_timestamps'], atol=1e-5, rtol=0):
                raise ValueError('Views require matching playback frame correspondence')
        rows[role] = {'channel': name, 'clock': c.clock, 'time_source': c.time_source,
                      'source_timestamps': list(c.timestamps), 'mapped_timestamps': t.tolist(),
                      'reference_clock': reference.clock, 'fps': fps,
                      'source_mapping': c.source_mapping}
        if playback is not None:
            rows[role].update(timing_policy=timing, playback_timestamps=list(playback),
                              clock_mapping=clock_mapping, time_evidence=getattr(c, 'time_evidence', {}))
    return rows


def materialize_views(context, rows):
    result = {}
    for role, row in rows.items():
        path = context.materialize_video(row['channel'], f'input/{role}.mp4')
        import cv2
        cap = cv2.VideoCapture(str(path))
        count, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        import numpy as np
        if count != len(row['mapped_timestamps']) or not np.isclose(fps,row['fps'],rtol=1e-4,atol=1e-5):
            raise ValueError(f'{role}: materialized video does not preserve the declared frame timeline')
        result[role] = {**row, 'path': path, 'evidence': context.video_evidence(row['channel'], path)}
    context.write_result('input-mapping.json', {'episode_id': context.episode.id,
        'source': str(context.episode.path), 'selector': context.episode.selector, 'views': result})
    return result
