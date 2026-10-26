#!/usr/bin/env python3
"""Compatibility CLI for the shared registry executor. See docs/framework/BATCH_MIGRATION.md."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    from edc.data.ir.dataset import open_dataset
    from edc.execution.runner import run
    from edc.algorithms.registry import algorithms
    parser = argparse.ArgumentParser(description='批处理通过统一注册表执行；输出 catalog.json / _runs')
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--profile')
    group.add_argument('--robot')
    parser.add_argument('--algorithm', action='append', help='注册的算法 ID，可重复')
    parser.add_argument('--params', help='JSON：算法 ID 到参数对象')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--fast-only', action='store_true')
    modes.add_argument('--video-only', action='store_true')
    parser.add_argument('--workers', type=int, default=1, help='当前统一执行器仅支持 1；旧并行调度已弃用')
    args = parser.parse_args(argv)
    if args.workers != 1:
        parser.error('旧 workers 并行调度已弃用，请使用 --workers 1；不会忽略该参数')
    if args.algorithm and (args.fast_only or args.video_only):
        parser.error('--algorithm cannot be combined with --fast-only/--video-only')
    try:
        output = Path(args.output)
        if any((output / name).exists() for name in ('fast.ndjson', 'video.ndjson', 'visual.ndjson')):
            raise ValueError('旧 ndjson 不能用作新执行器断点；请选择新的输出目录，旧结果保持原样')
        ids = args.algorithm
        if args.video_only:
            ids = ['Video_Integrity']
        elif args.fast_only:
            ids = [p.spec.id for p in algorithms().plugins.values()
                   if 'quality_cpu' in p.spec.presets and 'video' not in p.spec.required_inputs]
        print('batch_check 已接入统一执行器；不再默认运行 GPU 算法，不使用旧 ndjson 断点。', file=sys.stderr)
        with open_dataset(args.input, profile=args.profile, robot=args.robot) as dataset:
            result = run(dataset, output=args.output, algorithm_ids=ids,
                         params=json.loads(Path(args.params).read_text()) if args.params else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result['status_counts'].get('processing_error') else 0
    except Exception as exc:
        print(f'batch_check: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
