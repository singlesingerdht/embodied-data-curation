"""`edc batch`: many episode files in, one HTML report out.

Each file gets its own `edc run` process (all selected algorithms), all files at once by default.
A run writes its full output (records, report.html, evidence media) into a temporary folder; the
verdicts are summarized as soon as it finishes and the folder is deleted, unless --keep is given.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import edc
from edc.reporting.batch import DECISION, page, summarize

SUFFIXES = ('.bag',)


def repo_configs():
    """Checkout configs for editable installs or commands run at the repository root."""
    source = Path(edc.__file__).resolve().parents[2] / 'configs'
    for candidate in (source, Path.cwd() / 'configs'):
        if (candidate / 'robots').is_dir():
            return candidate
    return source


def resolve_robot(name):
    """configs/robots/<name>_signed.yaml (else <name>.yaml) and configs/robots/<name>/params.json if present."""
    robots = repo_configs() / 'robots'
    for candidate in (robots / f'{name}_signed.yaml', robots / f'{name}.yaml'):
        if candidate.is_file():
            params = robots / name / 'params.json'
            return candidate, params if params.is_file() else None
    raise ValueError(f'找不到机器人配置 {name}：{robots} 下没有 {name}_signed.yaml 或 {name}.yaml；可改用 --profile 指定文件')


def is_episode_dir(path):
    """A single-episode folder: trajectory JSON (data[] rows) next to its videos, e.g. X1 Pro robot-bridge."""
    from edc.data.readers.episode import _find_traj_json
    return path.is_dir() and _find_traj_json(path) is not None


def episode_dirs(root):
    """Episode folders under root (root itself included); an episode folder is not searched further."""
    if is_episode_dir(root):
        return [root]
    found = []
    for child in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')):
        found += episode_dirs(child)
    return found


def size_of(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.is_dir() else path.stat().st_size


def find_inputs(paths):
    files = []
    for path in map(Path, paths):
        if path.is_dir():
            found = sorted(p for p in path.rglob('*') if p.suffix in SUFFIXES and p.is_file())
            files += found or episode_dirs(path)
        elif path.is_file():
            files.append(path)
        else:
            raise ValueError(f'不存在：{path}')
    unique = list(dict.fromkeys(p.resolve() for p in files))
    names = Counter(p.name for p in unique)
    if any(n > 1 for n in names.values()):
        raise ValueError('有同名文件，报告里无法区分：' + ', '.join(k for k, n in names.items() if n > 1))
    return unique


def check_one(path, profile, params, keep, tmp):
    t0 = time.time()
    kept = Path(keep) / path.stem if keep else None
    work = Path(tempfile.mkdtemp(prefix='edc_batch_', dir=tmp))
    target = kept or work / 'out'
    try:
        cmd = [sys.executable, '-m', 'edc', 'run', str(path), '--profile', str(profile), '--output', str(target)]
        if params:
            cmd[5:5] = ['--params', str(params)]
        done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        names = Path(params).parent / 'names.json' if params else None  # Per-robot readable topic names.
        summary = summarize(target, json.loads(names.read_text(encoding='utf-8')) if names and names.is_file() else None)
        if summary is None or done.returncode:
            summary = {'decision': 'failed', 'algos': {}, 'log': '\n'.join(done.stdout.splitlines()[-25:])}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary.update(name=path.name, source=str(path), seconds=round(time.time() - t0, 1),
                   size_mb=round(size_of(path) / 2**20), kept=str(kept) if kept else None)
    return summary


def run_batch(inputs, *, profile=None, robot=None, params=None, jobs=None, output=None, keep=None,
              tmp=None, log=print):
    if robot:
        profile, default_params = resolve_robot(robot)
        params = params or default_params
    for path in filter(None, (profile, params)):
        if not Path(path).is_file():
            raise ValueError(f'找不到文件：{path}')
    files = find_inputs(inputs)
    if not files:
        raise ValueError('没有找到数据（' + '、'.join(SUFFIXES) + ' 文件或含轨迹 JSON 的单条数据目录）')
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=False)  # Never mix with an earlier run.
    jobs = jobs or len(files)
    label = robot or Path(profile).stem
    output = Path(output or f'{label}_report_{datetime.now():%Y%m%d_%H%M}.html')
    sizes = {p: size_of(p) for p in files}
    files.sort(key=sizes.get, reverse=True)  # Long ones first.
    log(f'找到 {len(files)} 条数据（共 {sum(sizes.values()) / 2**30:.1f} GB），同时跑 {jobs} 个')
    log(f'配置 {profile}' + (f'，参数 {params}' if params else ''))
    start, results = time.time(), []
    with ThreadPoolExecutor(jobs) as pool:
        for future in as_completed([pool.submit(check_one, p, profile, params, keep, tmp) for p in files]):
            r = future.result()
            results.append(r)
            log(f'[{len(results)}/{len(files)}] {time.time() - start:5.0f}s  {r["name"]}  '
                f'{DECISION.get(r["decision"], (r["decision"],))[0]}')
    info = {'robot': label, 'inputs': ' '.join(map(str, inputs)), 'jobs': jobs, 'seconds': time.time() - start,
            'finished': f'{datetime.now():%Y-%m-%d %H:%M}', 'profile': str(profile), 'params': str(params or '')}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page(results, info), encoding='utf-8')
    # The same verdicts as data, for tallies across many reports.
    output.with_suffix('.json').write_text(json.dumps({'info': info, 'results': results}, ensure_ascii=False),
                                           encoding='utf-8')
    counts = Counter(DECISION.get(r['decision'], (r['decision'],))[0] for r in results)
    log(f'\n全部完成，用时 {info["seconds"]:.0f} 秒。' + '，'.join(f'{k} {n} 个' for k, n in counts.items()))
    log(f'报告：{output.resolve()}')
    return {'report': str(output.resolve()), 'files': len(files),
            'failed': sum(r['decision'] == 'failed' for r in results)}
