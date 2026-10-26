"""Command line uses exactly the public dataset and executor APIs."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import yaml
from edc.data.ir.dataset import open_dataset
from edc.execution.runner import run
from edc.algorithms.registry import algorithms
from edc.robots.profiles import add_profile
from edc.reporting.results import report
from edc.algorithms.recommendations import recommend, recommend_episode, recommend_dataset


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def inspect(dataset, params=None, unit_policy="auto"):
    from edc.data.ir.units import SIDataset
    execution = SIDataset(dataset, unit_policy)
    result = dataset.describe()
    registry = algorithms()
    plugins = registry.plugins
    result['discovery_errors'] = registry.discovery_errors
    for item, episode in zip(result["episodes"], execution.iter_episodes()):
        if "error" not in item:
            with episode.opened():
                pool = recommend_episode(episode, plugins, params)
                item["algorithm_pool"] = pool
                item["algorithms"] = {e['algorithm']['id']: (e['status'], e['reason']) for e in pool['algorithms']}
                item["unit_normalization"] = episode.unit_evidence()
    result['dataset_algorithms'] = recommend_dataset(execution, plugins, params)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="edc")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("algorithms").add_argument('--diagnostics', action='store_true',
        help='Include entry-point import failures; default output remains the algorithm list')
    for command in ("inspect", "recommend", "run"):
        p = sub.add_parser(command)
        p.add_argument("input")
        p.add_argument("--backend", choices=["auto", "official", "compat"], default="auto")
        group = p.add_mutually_exclusive_group()
        group.add_argument("--profile")
        group.add_argument("--robot")
        p.add_argument("--params", help="JSON file mapping algorithm IDs to parameter objects")
        p.add_argument("--unit-policy", choices=["auto", "manual"], default="auto",
                       help="auto: convert declared source units to SI for algorithms; manual: disable conversion and require SI inputs")
        if command == "run":
            p.add_argument("--preset", default="quality_cpu")
            p.add_argument("--output", required=True)
            p.add_argument("--algorithm", action="append")
    p = sub.add_parser("convert-to-lerobot")
    p.add_argument("input")
    p.add_argument("--profile", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--fps", type=int, help="Explicit nominal frame-index FPS; never acquisition evidence")
    p.add_argument("--repo-id", default="local/edc-converted")
    p = sub.add_parser("report")
    p.add_argument("output")
    robot = sub.add_parser("robot").add_subparsers(dest="action", required=True)
    p = robot.add_parser("add")
    p.add_argument("profile")
    p = robot.add_parser("confirm-numeric")
    p.add_argument("--profile", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--params", help="Same algorithm parameter JSON file used by edc run")
    for action in ("init", "check"):
        p = robot.add_parser(action)
        p.add_argument("--input", required=True)
        if action == "init":
            p.add_argument("--id", required=True)
            p.add_argument("--output", required=True)
        else:
            p.add_argument("--profile", required=True)
    demo = sub.add_parser("demo").add_subparsers(dest="action", required=True)
    p = demo.add_parser("fetch")
    p.add_argument("name", choices=["pour_tea", "synthetic"])
    p.add_argument("--output", required=True)
    workflows = sub.add_parser('workflow').add_subparsers(dest='action', required=True)
    p = workflows.add_parser('check')
    p.add_argument('config')
    p = workflows.add_parser('run')
    p.add_argument('config')
    p.add_argument('--output', required=True)
    p.add_argument('--resume', action='store_true')
    configurations = sub.add_parser('config').add_subparsers(dest='action', required=True)
    for action in ('check', 'export', 'recommend', 'run'):
        p = configurations.add_parser(action)
        p.add_argument('config')
        if action in {'export', 'run'}:
            p.add_argument('--output', required=True)
        if action == 'recommend':
            p.add_argument('--stage')
        if action == 'run':
            p.add_argument('--resume', action='store_true')
    p = sub.add_parser('review')
    p.add_argument('output')
    p.add_argument('--run-id', required=True)
    p.add_argument('--algorithm', required=True)
    p.add_argument('--object-id', required=True)
    p.add_argument('--report-index', type=int, default=0)
    p.add_argument('--action', choices=['confirmed', 'dismissed'], required=True)
    p.add_argument('--reviewer', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--evidence', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'config':
            from edc.configuration import export_config, recommend_config
            from edc.workflows.validation import validate_workflow
            from edc.workflows.engine import run_workflow
            if args.action == 'export':
                emit({'output': str(export_config(args.config, args.output))})
            elif args.action == 'recommend':
                emit(recommend_config(args.config, args.stage))
            else:
                result = validate_workflow(args.config) if args.action == 'check' else run_workflow(args.config, args.output, resume=args.resume)
                emit(result)
                return 0 if result.get('valid') or result.get('delivery_status') == 'ready' else 1
        elif args.command == 'workflow':
            from edc.workflows.validation import validate_workflow
            from edc.workflows.engine import run_workflow
            result = (validate_workflow(args.config) if args.action == 'check' else
                      run_workflow(args.config, args.output, resume=args.resume))
            emit(result)
            return 0 if result.get('valid') or result.get('delivery_status') == 'ready' else 1
        elif args.command == 'review':
            from edc.workflows.reviews import review
            emit(review(args.output, run_id=args.run_id, algorithm=args.algorithm,
                object_id=args.object_id, report_index=args.report_index, action=args.action,
                reviewer=args.reviewer, reason=args.reason, evidence=args.evidence))
        elif args.command == "algorithms":
            registry = algorithms()
            items = [asdict(p.spec) for p in registry.plugins.values()]
            emit({'algorithms': items, 'discovery_errors': registry.discovery_errors} if args.diagnostics else items)
            if registry.discovery_errors and not args.diagnostics:
                print('edc: some plugins could not load; use edc algorithms --diagnostics', file=sys.stderr)
        elif args.command == "convert-to-lerobot":
            from edc.data.conversion.lerobot import convert_to_lerobot
            emit(convert_to_lerobot(args.input, profile=args.profile, output=args.output,
                                    fps=args.fps, repo_id=args.repo_id))
        elif args.command == "report":
            print(report(args.output))
        elif args.command == "demo":
            if args.name == "pour_tea":
                raise ValueError("真实倒茶数据尚无已确认的公开许可与下载清单；可用 edc demo fetch synthetic 验证流程")
            from edc.execution.examples import generate
            print(generate(args.output))
        elif args.command == "robot" and args.action == "add":
            print(add_profile(args.profile))
        elif args.command == "robot" and args.action == "confirm-numeric":
            from edc.robots.profiles import confirm_numeric_profile
            params = json.loads(Path(args.params).read_text()).get('Numeric_Check', {}) if args.params else None
            profile = confirm_numeric_profile(args.profile, args.reviewer, params)
            with Path(args.output).open('x') as stream:
                yaml.safe_dump(profile, stream, allow_unicode=True, sort_keys=False)
            print(args.output)
        elif args.command == "robot" and args.action == "init":
            with open_dataset(args.input) as dataset:
                description = dataset.describe()
            channels = {c["name"]: {"modality": c["modality"], "fields": c["fields"],
                "unit": None, "dimensions": None, "time_source": "unknown", "clock": None,
                "body_part": None, "side": "unknown", "source_kind": "unknown",
                "coordinate_frame": None, "calibration_status": "unknown", "joint_names": None,
                "field_metadata": {}, "sensor_id": None, "representation": {}}
                for ep in description["episodes"] for c in ep.get("channels", [])}
            profile = {"schema_version": 1, "id": args.id, "version": "0.1.0", "status": "draft",
                       "channels": channels, "calibration": {}, "scan_errors": [e for e in description["episodes"] if "error" in e]}
            from edc.robots.numeric_review import numeric_settings
            profile['numeric_check'] = numeric_settings()
            profile['numeric_review'] = {'status': 'pending', 'confirmed_by': None,
                                        'configuration_sha256': None}
            profile['unit_system_suggestion'] = 'SI'
            from edc.robots.profiles import load_profile
            load_profile(profile)
            with Path(args.output).open("x") as stream:
                yaml.safe_dump(profile, stream, allow_unicode=True, sort_keys=False)
            print(args.output)
        else:
            with open_dataset(args.input, profile=getattr(args, "profile", None), robot=getattr(args, "robot", None),
                              backend=getattr(args, "backend", "auto")) as dataset:
                if args.command == "run":
                    result = run(dataset, preset=args.preset, output=args.output, algorithm_ids=args.algorithm,
                        params=json.loads(Path(args.params).read_text()) if args.params else None,
                        on_preflight=lambda episode, statuses: emit({"episode": episode, "preflight": statuses}),
                        unit_policy=args.unit_policy)
                    emit(result)
                    return 1 if result["status_counts"].get("processing_error") else 0
                operation = recommend if args.command == 'recommend' else inspect
                result = operation(dataset, json.loads(Path(args.params).read_text()) if getattr(args, 'params', None) else None,
                                 unit_policy=getattr(args, "unit_policy", "auto"))
                emit(result)
                if args.command == "robot":
                    from edc.robots.validation import check_robot
                    issues = check_robot(dataset)
                    emit({"robot_check": "failed" if issues else "passed", "issues": issues})
                    return 1 if issues else 0
                return 1 if any("error" in e for e in result["episodes"]) else 0
        return 0
    except Exception as exc:
        print(f"edc: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
