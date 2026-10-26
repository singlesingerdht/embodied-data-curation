"""Shared Python/CLI executor with isolated episode and algorithm failures."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
import uuid
from framework.registry import algorithms
from framework.results import write_json, append_record, report
from framework.recommendations import recommend_episode, recommend_dataset


@dataclass
class Context:
    dataset: object
    episode: object
    workdir: Path
    media_cache: object = None
    upstream: object = None

    def __post_init__(self):
        from framework.workflows.references import freeze
        self.upstream = freeze(self.upstream or {})

    def materialize_video(self, channel, local_name):
        from framework.media import materialize
        return materialize(self, channel, local_name)

    def video_evidence(self, channel, inspection_input):
        from framework.media import video_evidence
        return video_evidence(self, channel, inspection_input)

    def write_result(self, name, value):
        target = (self.workdir / name).resolve()
        if not target.is_relative_to(self.workdir.resolve()):
            raise ValueError("Artifact must remain inside workdir")
        write_json(target, value)
        return str(target)


def run(dataset, preset="quality_cpu", output="results", algorithm_ids=None, params=None, on_preflight=None,
        unit_policy="auto", *, upstream=None):
    from framework.ir.units import SIDataset, UnitInputError, validate_unit_policy
    validate_unit_policy(unit_policy)
    execution_dataset = SIDataset(dataset, unit_policy)
    registry = algorithms()
    if preset not in {'quality_cpu'} | {name for p in registry.plugins.values() for name in p.spec.presets}:
        raise ValueError(f"Unknown preset: {preset}")
    ids = algorithm_ids if algorithm_ids is not None else [key for key, p in registry.plugins.items()
            if preset in p.spec.presets]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Select unique algorithm IDs")
    selected = [registry.plugins[key] for key in ids]
    episode_plugins = [p for p in selected if p.spec.granularity == 'episode']
    params = params or {}
    if not isinstance(params, dict) or any(key not in ids or not isinstance(value, dict) for key, value in params.items()):
        raise ValueError("params must map selected algorithm IDs to parameter objects")
    output = Path(output).resolve()
    # Source immutability includes preventing output over an input episode.
    for ep in dataset.iter_episodes():
        if output == ep.path or (ep.path.is_dir() and output.is_relative_to(ep.path)):
            raise ValueError("Output must not be inside an episode source")
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".dqs.lock"
    with lock.open("x"):
        pass
    try:
        catalog_path = output / "catalog.json"
        catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {"schema_version": "dqs_catalog_v1", "runs": []}
        if catalog.get("schema_version") != "dqs_catalog_v1":
            raise ValueError("Existing legacy catalog: select a separate output directory")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
        root = output / "_runs" / run_id
        root.mkdir(parents=True)
        inventory = [{"id": ep.id, "source": str(ep.path), "selector": ep.selector, "metadata": getattr(ep, "metadata", {}), "source_stat": source_stat(ep.path)} for ep in dataset.iter_episodes()]
        write_json(root / "run.json", {"schema_version": "dqs_run_v1", "run_id": run_id,
            "framework_version": "0.1.0a3", "dependencies": dependency_versions(selected), "dataset": {"id": dataset.id, "version": dataset.version,
                "source": str(dataset.path), "tasks": getattr(dataset, "tasks", []), "metadata": getattr(dataset, "metadata", {}), "episodes": inventory}, "profile": dataset.profile,
            "algorithms": [asdict(p.spec) for p in selected], "params": params,
            "discovery_errors": registry.discovery_errors,
            "upstream": upstream or {},
            "policy_version": "episode_filter_v1", "preset": preset,
            "recommendations": {"path": (root / 'recommendations.json').relative_to(output).as_posix(),
                "scope": "all_registered_algorithms", "selected_params": params},
            "unit_normalization": {"policy": unit_policy, "algorithm_unit_system": "SI",
                "source_profile_preserved": True, "threshold_units": "SI; thresholds are not rescaled"}})
        records_paths = []
        for plugin in selected:
            work = root / plugin.spec.id
            (work / "artifacts").mkdir(parents=True)
            for name in ("records.jsonl", "errors.jsonl"):
                (work / name).touch()
            records_paths.append((work / "records.jsonl").relative_to(output).as_posix())
        counts = {}
        selection = []
        recommendations = []
        from framework.media import MediaCache
        media_cache = MediaCache(root / '_media')
        for episode in execution_dataset.iter_episodes():
            records = []
            try:
                open_error = None
                try:
                    episode.open()
                except Exception as exc:
                    open_error = exc
                if open_error is None:
                    pool = recommend_episode(episode, registry.plugins, params, selected=ids)
                    recommendations.append(pool)
                    checks = {e['algorithm']['id']: (e['status'], e['reason']) for e in pool['algorithms']
                              if e['algorithm']['id'] in ids}
                else:
                    recommendations.append({'schema_version': 'dqs_recommendations_v1',
                        'episode_id': episode.id, 'error': str(open_error), 'algorithms': []})
                    checks = {p.spec.id: ("missing_dependency" if isinstance(open_error, ImportError) or type(open_error).__name__ == "DependencyMissing" else "processing_error", str(open_error)) for p in episode_plugins}
                if on_preflight:
                    on_preflight(episode.id, checks)
                for plugin in episode_plugins:
                    episode.reset_unit_evidence()
                    episode._clock_evidence = []
                    ident = plugin.spec.id
                    status, reason = checks[ident]
                    record = {"schema_version": "dqs_record_v1", "algorithm": {"id": ident, "version": plugin.spec.version},
                        "object": {"type": "episode", "id": episode.id, "source": str(episode.path)},
                        "status": status, "reason": reason, "result": None, "artifacts": []}
                    work = root / ident
                    key = hashlib.sha256(episode.id.encode()).hexdigest()[:20]
                    artifact_dir = work / "artifacts" / key
                    if status == "runnable":
                        try:
                            artifact_dir.mkdir()
                            from framework.algorithm_contracts import bind_parameters
                            effective, bindings = bind_parameters(plugin.spec, episode, params.get(ident, {}))
                            record['requested_params'] = params.get(ident, {})
                            record['channel_bindings'] = bindings
                            record['effective_params'] = effective
                            binding_hook = getattr(plugin, 'input_bindings', None)
                            if callable(binding_hook):
                                record['channel_bindings'] += binding_hook(episode, effective)
                            raw = plugin.run(Context(execution_dataset, episode, artifact_dir, media_cache, upstream), effective)
                            if not isinstance(raw, (dict, list)) or not raw:
                                raise ValueError("Plugin must return a nonempty JSON object or list")
                            reports = raw if isinstance(raw, list) else [raw]
                            if not all(isinstance(r, dict) for r in reports):
                                raise ValueError("Invalid result element")
                            if plugin.spec.category in {"editing", "augmentation"} and any(
                                not all(k in r for k in ("output", "params", "source_mapping")) for r in reports):
                                raise ValueError("Transform requires output, params and source_mapping")
                            original = artifact_dir / "result.json"
                            write_json(original, raw)
                            record["artifacts"] = [p.relative_to(output).as_posix() for p in sorted(artifact_dir.rglob("*")) if p.is_file()]
                            unavailable = [r for r in reports if "ir_unavailable" in r]
                            if unavailable:
                                record["status"] = ("missing_dependency" if any("dependency_missing" in r["ir_unavailable"] for r in unavailable)
                                                    else "missing_input" if all("need" in r["ir_unavailable"] for r in unavailable)
                                                    else "processing_error")
                                record["reason"] = "; ".join(str(r.get("reasons")) for r in unavailable)
                            else:
                                record["status"] = "completed"
                            record["result"] = {"reports": reports}
                        except Exception as exc:
                            record["status"] = ("missing_input" if isinstance(exc, UnitInputError) else
                                                "missing_dependency" if isinstance(exc, ImportError) else "processing_error")
                            record["reason"] = f"{type(exc).__name__}: {exc}"
                        # Failed workers can still produce logs, mappings and raw
                        # responses. Keep those discoverable for diagnosis.
                        record["artifacts"] = [p.relative_to(output).as_posix() for p in sorted(artifact_dir.rglob("*")) if p.is_file()]
                    if open_error is None:
                        artifact_dir.mkdir(exist_ok=True)
                        write_json(artifact_dir / "unit-normalization.json", episode.unit_evidence())
                        record["unit_normalization"] = {"policy": unit_policy, "algorithm_unit_system": "SI",
                            "evidence": (artifact_dir / "unit-normalization.json").relative_to(output).as_posix()}
                        record["artifacts"] = [p.relative_to(output).as_posix() for p in sorted(artifact_dir.rglob("*")) if p.is_file()]
                    record["clock_normalization"] = getattr(episode, "_clock_evidence", [])
                    append_record(work / "records.jsonl", record)
                    if record["status"] != "completed":
                        append_record(work / "errors.jsonl", record)
                    counts[record["status"]] = counts.get(record["status"], 0) + 1
                    records.append(record)
            finally:
                episode.close()
            quality = [r for r in records if registry.plugins[r["algorithm"]["id"]].spec.category == "quality"]
            verdicts = [report.get("verdict") for r in quality if r["status"] == "completed"
                        for report in r["result"]["reports"]]
            complete = bool(quality) and all(r["status"] == "completed" and
                all(item.get("assessment_complete", True) for item in r["result"]["reports"]) for r in quality)
            decision = "exclude" if "DO_NOT_USE" in verdicts else "review" if "NEEDS_REVIEW" in verdicts else "keep" if complete and verdicts and all(v == "ACCEPTABLE" for v in verdicts) else "unassessed"
            selection.append({"episode_id": episode.id, "source": str(episode.path), "decision": decision,
                              "assessment_complete": complete, "policy_version": "episode_filter_v1"})
        dataset_checks = recommend_dataset(execution_dataset, registry.plugins, params, selected=ids)
        try:
            for check in dataset_checks:
                if not check['selected']:
                    continue
                plugin = registry.plugins[check['algorithm']['id']]
                if on_preflight:
                    on_preflight(dataset.id, {plugin.spec.id: (check['status'], check['reason'])})
                record = run_dataset_plugin(plugin, execution_dataset, root, output, check, media_cache, upstream)
                counts[record['status']] = counts.get(record['status'], 0) + 1
        finally:
            for episode in execution_dataset.iter_episodes():
                episode.close()
        write_json(root / "selection.json", selection)
        write_json(root / "recommendations.json", {'schema_version': 'dqs_recommendation_pool_v1',
            'episodes': recommendations, 'dataset_algorithms': dataset_checks,
            'discovery_errors': registry.discovery_errors})
        summary = {"run_id": run_id, "episodes": len(selection), "status_counts": counts,
            "status": "partial_success" if any(k != "completed" for k in counts) else "completed"}
        write_json(root / "summary.json", summary)
        catalog["runs"].append({"run_id": run_id, "records": records_paths,
            **({'media_manifest': (media_cache.root / 'manifest.json').relative_to(output).as_posix()}
               if (media_cache.root / 'manifest.json').is_file() else {}),
            "run": (root / "run.json").relative_to(output).as_posix(),
            "summary": (root / "summary.json").relative_to(output).as_posix(),
            "recommendations": (root / "recommendations.json").relative_to(output).as_posix(),
            "selection": (root / "selection.json").relative_to(output).as_posix()})
        write_json(catalog_path, catalog)
        report(output)
        return summary
    finally:
        lock.unlink(missing_ok=True)


def run_dataset_plugin(plugin, dataset, root, output, check, media_cache, upstream=None):
    """One call for the whole SI dataset; never synthesize per-episode scores."""
    from framework.ir.units import UnitInputError
    work = root / plugin.spec.id
    artifact_dir = work / 'artifacts' / 'dataset'
    artifact_dir.mkdir()
    record = {'schema_version': 'dqs_record_v1', 'algorithm': check['algorithm'],
        'object': {'type': 'dataset', 'id': dataset.id, 'source': str(dataset.path)},
        'status': check['status'], 'reason': check['reason'], 'result': None,
        'effective_params': check['effective_params'], 'artifacts': []}
    for episode in dataset.iter_episodes():
        episode.reset_unit_evidence()
    try:
        if record['status'] == 'runnable':
            raw = plugin.run(Context(dataset, None, artifact_dir, media_cache, upstream), check['effective_params'])
            items = raw if isinstance(raw, list) else [raw]
            if not items or any(not isinstance(r, dict) or not r for r in items):
                raise ValueError('Plugin must return a nonempty JSON object or list of objects')
            if plugin.spec.category in {'editing', 'augmentation'} and any(
                    not all(k in r for k in ('output', 'params', 'source_mapping')) for r in items):
                raise ValueError('Transform requires output, params and source_mapping')
            write_json(artifact_dir / 'result.json', raw)
            record['result'] = {'reports': items}
            unavailable = [r for r in items if 'ir_unavailable' in r]
            record['status'] = ('missing_dependency' if any('dependency_missing' in r['ir_unavailable'] for r in unavailable)
                else 'missing_input' if unavailable and all('need' in r['ir_unavailable'] for r in unavailable)
                else 'processing_error' if unavailable else 'completed')
            if unavailable:
                record['reason'] = '; '.join(str(r.get('reasons')) for r in unavailable)
    except Exception as exc:
        record['status'] = ('missing_input' if isinstance(exc, UnitInputError) else
                            'missing_dependency' if isinstance(exc, ImportError) else 'processing_error')
        record['reason'] = f'{type(exc).__name__}: {exc}'
    finally:
        evidence = []
        for episode in dataset.iter_episodes():
            try:
                evidence.append({'episode_id': episode.id, 'evidence': episode.unit_evidence()})
            except Exception as exc:
                evidence.append({'episode_id': episode.id, 'status': 'unavailable',
                    'reason': f'{type(exc).__name__}: {exc}'})
            finally:
                episode.close()
        write_json(artifact_dir / 'unit-normalization.json', {'unit_system': 'SI',
            'episodes': evidence})
    record['artifacts'] = [p.relative_to(output).as_posix() for p in sorted(artifact_dir.rglob('*')) if p.is_file()]
    append_record(work / 'records.jsonl', record)
    if record['status'] != 'completed':
        append_record(work / 'errors.jsonl', record)
    return record


def source_stat(path):
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    return [{"path": str(p.relative_to(path)) if p != path else p.name,
             "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in files]


def dependency_versions(plugins):
    names = {"numpy", "PyYAML", "embodied-dqs"}
    names.update(dep for p in plugins for dep in p.spec.dependencies)
    result = {}
    for name in sorted(names):
        try:
            result[name] = version({"cv2": "opencv-python-headless"}.get(name, name))
        except PackageNotFoundError:
            result[name] = "not-installed-or-unmapped"
    return result
