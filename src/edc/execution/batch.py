"""`edc batch`: many episode files in, one HTML report out.

Each file gets its own `edc run` process (all selected algorithms), 16 files at a time by default.
A run writes JSON records and evidence, without per-episode HTML, into a temporary folder; the
verdicts are summarized as soon as it finishes and the folder is deleted, unless --keep is given.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from edc.reporting.batch import DECISION, page, summarize

DEFAULT_JOBS = 16  # Without --jobs (shared servers).


def resolve_robot(name):
    """--robot 只收机器人 yaml 的路径，如 configs/robots/x1pro.yaml。"""
    path = Path(name)
    if path.suffix not in ('.yaml', '.yml'):
        raise ValueError(f'--robot 要写机器人 yaml 的路径，如 configs/robots/{name}.yaml')
    if not path.is_file():
        raise ValueError(f'找不到机器人配置：没有 {path}')
    return path


def robot_settings(profile):
    """(algorithm parameters, readable channel names) written in a robot yaml; empty for a full profile."""
    from edc.robots.card import is_card, compile_card
    from edc.data.channel_registry import read_unique_yaml
    data = read_unique_yaml(Path(profile).read_text(encoding='utf-8'))
    if not is_card(data):
        return {}, {}
    _, params, names = compile_card(data, Path(profile).resolve().parent)
    return params, names


def sign(profile):
    """yaml 除注释外改过（或没签名）就在终端里问签名人，把 confirmed_by、signature 两行写回 yaml（只改这两行，注释保留）。"""
    from edc.robots.card import is_card, card_digest
    from edc.data.channel_registry import read_unique_yaml
    data = read_unique_yaml(Path(profile).read_text(encoding='utf-8'))
    if not is_card(data) or data['robot'].get('signature') == card_digest(data):
        return
    robot = data['robot']
    if not sys.stdin.isatty():
        raise ValueError(f'{profile} 改过或还没签名：请在终端里运行 edc batch，按提示输入签名人')
    try:
        who = input(f'{profile} 改过或还没签名。核对无误后输入签名人（直接回车 = {robot["confirmed_by"]}，Ctrl+C 取消）：')
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('\n已取消，没有签名')
    robot['confirmed_by'] = who.strip() or robot['confirmed_by']
    text = re.sub(r'^[ \t]+signature:.*\n', '', Path(profile).read_text(encoding='utf-8'), flags=re.M)
    text = re.sub(r'^([ \t]+)confirmed_by:[^#\n]*', lambda m: f"{m[1]}confirmed_by: {robot['confirmed_by']}  ", text,
                  count=1, flags=re.M)
    text = re.sub(r'^([ \t]+)confirmed_by:.*$', lambda m: f'{m[0].rstrip()}\n{m[1]}signature: {card_digest(data)}', text,
                  count=1, flags=re.M)
    Path(profile).write_text(text, encoding='utf-8')
    print(f'✓ 已签名：{robot["confirmed_by"]}')


def size_of(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.is_dir() else path.stat().st_size


def find_items(paths, profile):
    """(source, k): every episode the framework finds under the inputs, in any supported format.
    k is the episode's position inside its source when the source holds several (LeRobot, multi-group HDF5)."""
    from edc.data.ir.dataset import open_dataset
    sources = {}
    for path in map(Path, paths):
        if not path.exists():
            raise ValueError(f'不存在：{path}')
        for episode in open_dataset(path, profile=profile).iter_episodes():
            sources.setdefault(episode.path.resolve(), []).append(episode.id)
    items = []
    for source, ids in sources.items():
        if len(ids) == 1:
            items.append((source, None))
        else:  # Same order as `edc run --episode k` sees when it opens this source.
            items += [(source, k) for k in range(len(ids))]
    names = Counter(source.name for source, k in items if k is None)
    if any(n > 1 for n in names.values()):
        raise ValueError('有同名文件，报告里无法区分：' + ', '.join(k for k, n in names.items() if n > 1))
    return items


def _algorithm_errors(summary):
    """An error can coexist with a more severe data verdict in the same algorithm."""
    return any(i['status'] == 'failed' for r in summary['algos'].values() for i in r['items'])


def check_one(item, profile, keep, tmp):
    path, episode = item
    label = f'{path.name}:{episode}' if episode is not None else path.name
    t0 = time.time()
    kept = Path(keep) / label.replace(':', '_ep') if keep else None
    work = Path(tempfile.mkdtemp(prefix='edc_batch_', dir=tmp))
    target = kept or work / 'out'
    try:
        cmd = [sys.executable, '-m', 'edc', 'run', str(path), '--profile', str(profile), '--output', str(target),
               '--no-html']
        if episode is not None:
            cmd += ['--episode', str(episode)]
        done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        error = ''
        try:
            summary = summarize(target)
            if summary is None:
                error = '没有找到 quality.json 结果'
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            summary = None
            error = f'无法读取 quality.json：{type(exc).__name__}: {exc}'
        if summary is None:
            summary = {'decision': 'failed', 'algos': {}}
        elif done.returncode and not _algorithm_errors(summary):
            summary['decision'] = 'failed'
            error = f'edc run 异常退出（退出码 {done.returncode}），已有算法结果保留'
        summary['returncode'] = done.returncode
        if error or done.returncode:
            summary['log'] = '\n'.join(filter(None, [error, '\n'.join(done.stdout.splitlines()[-25:])]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary.update(name=label, source=str(path) + (f' 第 {episode} 条' if episode is not None else ''),
                   seconds=round(time.time() - t0, 1), size_mb=round(size_of(path) / 2**20) if episode is None else None,
                   kept=str(kept) if kept else None)
    return summary


def run_batch(inputs, *, profile=None, robot=None, jobs=None, output=None, keep=None, tmp=None, log=print):
    profile = resolve_robot(robot) if robot else Path(profile)
    if not profile.is_file():
        raise ValueError(f'找不到文件：{profile}')
    robot_settings(profile)  # 先核对 yaml 写得对不对
    sign(profile)            # 改过就当场签名（只在 edc batch 里问）
    items = find_items(inputs, profile)
    if not items:
        raise ValueError('没有找到数据')
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=False)  # Never mix with an earlier run.
    jobs = jobs or DEFAULT_JOBS
    label = profile.stem
    output = Path(output or f'{label}_report_{datetime.now():%Y%m%d_%H%M}.html')
    sizes = {source: size_of(source) for source in {s for s, _ in items}}
    items.sort(key=lambda item: sizes[item[0]], reverse=True)  # Long ones first.
    log(f'找到 {len(items)} 条数据（{len(sizes)} 个文件/目录，共 {sum(sizes.values()) / 2**30:.1f} GB），同时跑 {jobs} 个')
    log(f'配置 {profile}')
    start, results = time.time(), []
    with ThreadPoolExecutor(jobs) as pool:
        for future in as_completed([pool.submit(check_one, item, profile, keep, tmp) for item in items]):
            r = future.result()
            results.append(r)
            log(f'[{len(results)}/{len(items)}] {time.time() - start:5.0f}s  {r["name"]}  '
                f'{DECISION.get(r["decision"], (r["decision"],))[0]}')
    info = {'robot': label, 'inputs': ' '.join(map(str, inputs)), 'jobs': jobs, 'seconds': time.time() - start,
            'finished': f'{datetime.now():%Y-%m-%d %H:%M}', 'profile': str(profile)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page(results, info), encoding='utf-8')
    # The same verdicts as data, for tallies across many reports.
    output.with_suffix('.json').write_text(json.dumps({'info': info, 'results': results}, ensure_ascii=False),
                                           encoding='utf-8')
    counts = Counter(DECISION.get(r['decision'], (r['decision'],))[0] for r in results)
    log(f'\n全部完成，用时 {info["seconds"]:.0f} 秒。' + '，'.join(f'{k} {n} 个' for k, n in counts.items()))
    log(f'报告：{output.resolve()}')
    return {'report': str(output.resolve()), 'files': len(items),
            'failed': sum(bool(r['decision'] == 'failed' or r.get('returncode') or _algorithm_errors(r))
                          for r in results)}
