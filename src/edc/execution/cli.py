"""edc command line.

User side:      edc channels <data>                          what is in the data (names for the robot yaml)
                edc robot check --robot R --data D           does the robot yaml match the data
                edc robot urdf --urdf U [--base B --tip T]   names in a URDF (to write the yaml's urdf part)
                edc robot urdf --robot R --data D [D2]       verify the yaml's urdf part with two recorded episodes
                edc batch <data>... --robot R                run every quality algorithm, one HTML report
Developer side: edc algorithms                               registered algorithms (built-in and installed)
                edc run <data> --robot R --output O          one source, full output folder (used by edc batch)
"""
import argparse
import json
import sys
from pathlib import Path

# Default shortest episode (user decision 2026-10-30); the robot yaml's data.min_duration_s overrides it.
CLI_MIN_DURATION_S = 3.0


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def robot_path(name):
    from edc.execution.batch import resolve_robot
    return resolve_robot(name)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='edc', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('channels', help='List what the reader finds in the data')
    p.add_argument('data')
    robot = sub.add_parser('robot', help='Check a robot yaml or a URDF').add_subparsers(dest='action', required=True)
    p = robot.add_parser('check', help='Read every channel the robot yaml declares from one episode')
    p.add_argument('--robot', required=True, help='机器人 yaml 的路径，如 configs/robots/x1pro.yaml')
    p.add_argument('--data', required=True)
    p = robot.add_parser('urdf', help='List a URDF (--urdf), or verify the urdf part of a robot yaml (--robot --data)')
    p.add_argument('--urdf', help='URDF file to list')
    p.add_argument('--base', help='With --urdf: start link; prints the moving joints from it to --tip')
    p.add_argument('--tip', help='With --urdf: end link')
    p.add_argument('--robot', help='机器人 yaml 的路径，核对它的 urdf 段')
    p.add_argument('--data', nargs='+', help='两条数据（第一条求坐标系，第二条验证）；一个文件里有多条数据时写一个就够')
    p.add_argument('--arm', action='append', help='Only these arms (default: every arm with urdf)')
    p = sub.add_parser('batch', help='Run all algorithms on every episode under the inputs, in parallel, into one HTML report')
    p.add_argument('inputs', nargs='+', help='Folders (searched recursively) or single files')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--robot', help='机器人 yaml 的路径，如 configs/robots/x1pro.yaml')
    group.add_argument('--profile', help='Robot yaml path')
    p.add_argument('--params', help='JSON {algorithm: {parameter: value}}; overrides the yaml checks section')
    p.add_argument('--jobs', type=int, help='Episodes run at the same time (default: 16)')
    p.add_argument('--output', help='Report file (default: <robot>_report_<time>.html)')
    p.add_argument('--keep', help='Keep the full run output of every episode under this new folder')
    p.add_argument('--tmp', help='Where temporary run folders go (default: system temp)')
    sub.add_parser('algorithms', help='List registered quality algorithms')
    p = sub.add_parser('run', help='One source, full output folder')
    p.add_argument('input')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--profile')
    group.add_argument('--robot')
    p.add_argument('--params', help='JSON {algorithm: {parameter: value}}')
    p.add_argument('--output', required=True)
    p.add_argument('--algorithm', action='append', help='Only these algorithms')
    p.add_argument('--episode', type=int, help='Only the k-th episode (0-based) found in the input')
    p.add_argument('--backend', choices=['auto', 'official', 'compat'], default='auto')
    p = sub.add_parser('regenerate', help='根据质检 JSON 报告筛选并重生成数据集（复制或软链接）')
    p.add_argument('report', help='edc batch 写出的 JSON 报告')
    p.add_argument('--output', required=True, help='新数据集目录（必须为空或不存在）')
    p.add_argument('--mode', choices=['copy', 'link'], default='copy',
                   help='copy=复制（可独立使用）；link=软链接（依赖源数据持续存在）')
    p.add_argument('--include', help='要选择的状态，逗号分隔（默认 keep；可显式选其它状态，原结论保留）')
    p.add_argument('--profile', help='解析 episode 身份/selector 用的机器人 yaml（可选）')
    p.add_argument('--preview', action='store_true', help='只展示选择数量、源路径、目标路径和操作方式，不写入')
    args = parser.parse_args(argv)
    try:
        if args.command == 'channels':
            from edc.robots.yaml_check import list_channels
            list_channels(args.data)
        elif args.command == 'robot' and args.action == 'check':
            from edc.robots.yaml_check import check_yaml
            return 1 if check_yaml(robot_path(args.robot), args.data) else 0
        elif args.command == 'robot' and args.action == 'urdf':
            from edc.robots.urdf_check import list_urdf, verify
            if args.urdf and not args.robot:
                if bool(args.base) != bool(args.tip):
                    parser.error('--base 和 --tip 要一起写')
                return list_urdf(args.urdf, args.base, args.tip)
            if not (args.robot and args.data) or args.urdf:
                parser.error('写 --urdf 文件（查名字），或写 --robot 机型 --data 数据（核对 yaml 里的 urdf）')
            result = verify(robot_path(args.robot), args.data, args.arm)
            return 0 if result and all(r.get('verdict') != 'mismatch' for r in result.values()) else 1
        elif args.command == 'batch':
            from edc.execution.batch import run_batch
            result = run_batch(args.inputs, profile=args.profile, robot=args.robot, params=args.params,
                               jobs=args.jobs, output=args.output, keep=args.keep, tmp=args.tmp)
            return 1 if result['failed'] else 0
        elif args.command == 'algorithms':
            from edc.algorithms.registry import algorithms
            for spec in (p.spec for p in algorithms().plugins.values()):
                print(f'{spec.id:24} {spec.version:8} {spec.name}')
        elif args.command == 'run':
            return run_one(args)
        elif args.command == 'regenerate':
            from edc.data.regenerate import regenerate
            include = tuple(x.strip() for x in args.include.split(',')) if args.include else ('keep',)
            result = regenerate(args.report, args.output, mode=args.mode, include=include,
                                profile=args.profile, dry_run=args.preview,
                                log=lambda text: print(text, flush=True))
            return 1 if result.get('failed') else 0
        return 0
    except Exception as exc:
        print(f'edc: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


def run_one(args):
    from edc.data.ir.canonical import open_dataset
    from edc.execution.runner import run
    from edc.execution.batch import robot_settings
    from edc.algorithms.quality.adapters import cpu
    profile = robot_path(args.robot) if args.robot else args.profile
    params = robot_settings(profile)[0] if profile else {}
    for algo, values in (json.loads(Path(args.params).read_text(encoding='utf-8')) if args.params else {}).items():
        params.setdefault(algo, {}).update(values)
    if args.algorithm:
        params = {k: v for k, v in params.items() if k in args.algorithm}
    with open_dataset(args.input, profile=profile, backend=args.backend) as dataset:
        if args.episode is not None:
            episodes = list(dataset.iter_episodes())
            if not 0 <= args.episode < len(episodes):
                raise ValueError(f'--episode {args.episode}: only {len(episodes)} episodes')
            dataset._episodes = [episodes[args.episode]]
        saved, cpu.DEFAULT_MIN_DURATION_S = cpu.DEFAULT_MIN_DURATION_S, CLI_MIN_DURATION_S
        try:
            result = run(dataset, output=args.output, algorithm_ids=args.algorithm, params=params or None)
        finally:
            cpu.DEFAULT_MIN_DURATION_S = saved
    emit(result)
    return 1 if result['status_counts'].get('processing_error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
