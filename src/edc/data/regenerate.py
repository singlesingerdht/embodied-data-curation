"""根据质检 JSON 报告筛选并重生成数据集。

只搬运数据、写清单和 `dataset.yaml`，不重新计算任何算法结论，也不做格式标准化或数据修复。
输入报告为 `edc batch` 写出的 JSON（`info` + `results[]`，每条含 `decision`／`name`／`source`）。

`edc batch` 把共享源（一个文件/目录里有多条 episode）写成 ``<source> 第 N 条``，`name` 写成
``<basename>:N``；独立 episode 的 `source` 就是纯路径。这里按 `source` 里的序号重新定位 episode。
"""

from __future__ import annotations

from edc.schemas import REGENERATE_MANIFEST

import copy
import json
import os
import re
import shutil
from pathlib import Path

MANIFEST_SCHEMA = REGENERATE_MANIFEST
# 决策状态与 reporting.batch.DECISION 一致（keep/salvage/review/unassessed/exclude/failed）；
# 未知状态按字面值处理，绝不改写。
DEFAULT_INCLUDE = ("keep",)
RESERVED_NAMES = {"dataset.yaml", "manifest.json"}


class RegenerateError(ValueError):
    """无法正确导出时抛出；边界写进消息。"""


def _read_report(report):
    path = Path(report)
    if not path.is_file():
        raise RegenerateError(f"报告不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegenerateError(f"报告不是有效 JSON：{type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise RegenerateError("报告缺少 results[]（应为 edc batch 的 JSON 报告结构）")
    return path, payload


_EPISODE_INDEX_RE = re.compile(r"^(.*)\s+第\s*(\d+)\s*条$")


def _parse_source(source):
    """拆出报告 `source` 里的真实路径和（可选的）episode 序号；独立 episode 就是纯路径。"""
    if not isinstance(source, str) or not source.strip():
        return None, None
    m = _EPISODE_INDEX_RE.match(source)
    if m:
        return m.group(1), int(m.group(2))
    return source, None


def _resolve_episode(source: Path, episode_index, profile):
    """按框架对 Dataset/Episode 的解析得到稳定 ID、格式和 selector。

    ``episode_index`` 是报告 `source` 里 “第 N 条” 的 0 起序号；None 表示独立 episode。
    返回 ``(episode, shared)``；shared=True 表示 source 是含多条 episode 的共享容器，
    需要用 selector 保留 Episode 选择范围。
    """
    from edc.data.ir.canonical import open_dataset

    with open_dataset(str(source), profile=profile) as ds:
        episodes = list(ds.iter_episodes())
    if not episodes:
        raise RegenerateError(f"{source} 没有可解析的 episode")
    if episode_index is not None:
        if not 0 <= episode_index < len(episodes):
            raise RegenerateError(f"{source} 只有 {len(episodes)} 条 episode，第 {episode_index} 条越界")
        ep = episodes[episode_index]
        if "discovery_error" in ep.selector:
            raise RegenerateError(f"{source} 第 {episode_index} 条无法解析：{ep.selector['discovery_error']}")
        if len(episodes) > 1 and Path(ep.path).resolve() != source.resolve():
            # 一个目录里的多条独立 episode（各自在子目录），不是“共享文件”；逐条导出才正确。
            raise RegenerateError(
                f"{source} 的第 {episode_index} 条 episode 在子目录 {ep.path} 里，不是共享文件；"
                "请逐条指定 episode 子目录导出")
        return ep, len(episodes) > 1
    if len(episodes) > 1:
        raise RegenerateError(
            f"{source} 是包含 {len(episodes)} 条 episode 的共享源，但报告没写第几条；"
            "请用 edc batch 重新生成带序号的报告")
    ep = episodes[0]
    if "discovery_error" in ep.selector:
        raise RegenerateError(f"{source} 无法解析：{ep.selector['discovery_error']}")
    return ep, False


def _plan(report, *, include, profile):
    """解析报告并规划每个选中条目：源、目标、episode 身份、操作方式。"""
    report_path, payload = _read_report(report)
    results = payload["results"]
    entries, not_selected, failures = [], [], []

    for index, item in enumerate(results):
        if not isinstance(item, dict):
            failures.append({"name": None, "decision": None, "failure": f"第 {index} 项不是对象"})
            continue
        name, decision, source = item.get("name"), item.get("decision"), item.get("source")
        if decision not in include:
            not_selected.append({"name": name, "decision": decision, "source": source,
                                 "reason": "决策不在筛选范围" if decision is not None else "缺少 decision"})
            continue
        raw_path, episode_index = _parse_source(source)
        if not raw_path:
            failures.append({"name": name, "decision": decision, "failure": "缺少 source 源路径"})
            continue
        src = Path(raw_path)
        if not src.is_absolute():
            src = (report_path.parent / raw_path).resolve()
        if not src.exists():
            failures.append({"name": name, "decision": decision, "source": str(src),
                             "failure": f"源路径不存在：{src}"})
            continue
        if not name or Path(name).name != name:
            failures.append({"name": name, "decision": decision, "source": str(src),
                             "failure": f"名称无效或含路径分隔符：{name!r}"})
            continue
        if name in RESERVED_NAMES:
            failures.append({"name": name, "decision": decision, "source": str(src),
                             "failure": f"名称与保留文件冲突：{name}"})
            continue
        try:
            ep, shared = _resolve_episode(src, episode_index, profile)
        except RegenerateError as exc:
            failures.append({"name": name, "decision": decision, "source": str(src), "failure": str(exc)})
            continue
        except Exception as exc:  # 解析失败不阻塞其它条目，但如实记录
            failures.append({"name": name, "decision": decision, "source": str(src),
                             "failure": f"解析 episode 失败：{type(exc).__name__}: {exc}"})
            continue
        entries.append({
            "name": name,
            "decision": decision,
            "source": str(src),
            "source_is_shared": shared,
            "episode_id": ep.id,
            "format": ep.format,
            "selector": copy.deepcopy(ep.selector),
            "metadata": copy.deepcopy(ep.metadata),
        })

    counts: dict[str, int] = {}
    for e in entries:
        counts[e["name"]] = counts.get(e["name"], 0) + 1
    dup = {n: k for n, k in counts.items() if k > 1}
    if dup:
        raise RegenerateError("同名冲突：" + ", ".join(f"{n}(×{k})" for n, k in dup.items()))

    # 目标命名：独立 episode 用报告名；共享源整体复制/链接一次，用源自身名称，冲突时加后缀。
    target_of, source_of = {}, {}
    used = set(RESERVED_NAMES)
    groups: dict[str, list] = {}
    for e in entries:
        groups.setdefault(str(Path(e["source"]).resolve()), []).append(e)
    for key, group in groups.items():
        source = Path(group[0]["source"]).resolve()
        shared = group[0]["source_is_shared"]
        rel = group[0]["name"] if (not shared and len(group) == 1) else (source.name or "_shared")
        base, i = rel, 1
        while rel in used:
            rel = f"{base}_{i}"
            i += 1
        used.add(rel)
        source_of[rel] = source
        for e in group:
            target_of[e["name"]] = rel

    return report_path, entries, not_selected, failures, target_of, source_of


def _render_plan(entries, not_selected, failures, mode, include, log):
    log(f"筛选条件：decision ∈ {', '.join(include)}，模式 {mode}")
    if not entries:
        log("0 条匹配筛选条件。")
    else:
        log(f"将选择 {len(entries)} 条，未选择 {len(not_selected)} 条，解析失败 {len(failures)} 条：")
        for e in entries:
            log(f"  {e['decision']:8} {e['name']}")
            log(f"      源 {e['source']}")
            log(f"      → {e['target']}  [{mode}]")
    for f in failures:
        log(f"  失败 {f.get('name')!r}：{f['failure']}")


def _materialize(source: Path, target: Path, mode):
    if mode == "copy":
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    elif mode == "link":
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(source, target)
        except FileExistsError as exc:
            raise RegenerateError(f"目标已存在：{target}") from exc
    else:
        raise RegenerateError(f"未知模式：{mode}")


def _write_manifest(output, report_path, mode, include, entries, target_of, not_selected, failures,
                    export_failures):
    failed_targets = {f["target"] for f in export_failures}
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "report_source": str(report_path),
        "filter": {"include": list(include), "mode": mode},
        "selected": len(entries),
        "not_selected": len(not_selected),
        "failed": len(failures) + len(export_failures),
        "episodes": [
            {
                "name": e["name"],
                "original_decision": e["decision"],
                "source": e["source"],
                "target": target_of[e["name"]],
                "mode": mode,
                "episode_id": e["episode_id"],
                "format": e["format"],
                "selector": e["selector"],
                "status": "failed" if target_of[e["name"]] in failed_targets else "exported",
                "failure": next((f["failure"] for f in export_failures
                                 if f["target"] == target_of[e["name"]]), None),
            }
            for e in entries
        ],
        "not_selected": not_selected,
        "failed": failures + export_failures,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_dataset_yaml(output, entries, target_of):
    """写 dataset.yaml，让 open_dataset 能按稳定 ID 和 selector 重新读取选中的 episode。

    软链接模式不写：其解析依赖 discover 跟随软链接，避免 open_dataset 对 path 的
    resolve() 把链接解析到输出目录之外。
    """
    episodes = []
    for e in entries:
        episodes.append({
            "id": e["episode_id"],
            "path": target_of[e["name"]],
            "format": e["format"],
            "selector": copy.deepcopy(e["selector"]),
            "metadata": copy.deepcopy(e["metadata"]),
        })
    doc = {"schema_version": 1, "episodes": episodes}
    from edc.reporting.results import write_json
    write_json(output / "dataset.yaml", doc)


def _verify(output, expected_ids, profile):
    """用框架重新读取输出，返回实际读到的 episode 稳定 ID 列表。"""
    from edc.data.ir.canonical import open_dataset

    with open_dataset(str(output), profile=profile) as ds:
        return [ep.id for ep in ds.iter_episodes()]


def regenerate(report, output, *, mode="copy", include=DEFAULT_INCLUDE, profile=None,
               dry_run=False, log=print):
    """根据质检报告筛选并重生成数据集。

    - ``mode``：``copy``（复制，可独立使用）或 ``link``（软链接，依赖源数据持续存在）。
    - ``include``：要选择的一批 ``decision`` 状态，默认只选 ``keep``；原结论原样写入清单，绝不改写。
    """
    if mode not in ("copy", "link"):
        raise RegenerateError(f"未知模式：{mode}")
    include = tuple(include) if include else DEFAULT_INCLUDE
    report_path, entries, not_selected, failures, target_of, source_of = _plan(
        report, include=include, profile=profile)

    if mode == "link" and any(e["source_is_shared"] for e in entries):
        names = ", ".join(e["name"] for e in entries if e["source_is_shared"])
        raise RegenerateError(
            f"软链接模式无法正确导出共享源里的单条 episode（会把源包含的所有 episode 一并暴露）：{names}；"
            "请改用 --mode copy，或逐条导出")

    output = Path(output).resolve()
    preview_entries = [
        {**{k: e[k] for k in ("name", "decision", "source")},
         "target": str(output / target_of[e["name"]]), "mode": mode}
        for e in entries
    ]

    def summary(exported=0, manifest=None, verification=None, failures=failures):
        return {
            "schema_version": MANIFEST_SCHEMA,
            "report": str(report_path), "output": str(output), "mode": mode,
            "include": list(include), "selected": len(entries), "exported": exported,
            "failed": len(failures), "not_selected": len(not_selected),
            "manifest": manifest, "verification": verification,
            "entries": preview_entries, "not_selected_items": not_selected, "failures": failures,
        }

    if dry_run:
        _render_plan(preview_entries, not_selected, failures, mode, include, log)
        return summary()

    # 全新目录；拒绝非空输出，也拒绝把输出放进任一源目录里（否则复制会递归）。
    for source in source_of.values():
        if output == source or output.is_relative_to(source):
            raise RegenerateError(f"输出目录不能位于源数据内：{output}（源 {source}）")
    if output.exists() and any(output.iterdir()):
        raise RegenerateError(f"输出目录非空，拒绝写入（避免覆盖）：{output}")

    if not entries:
        _render_plan(preview_entries, not_selected, failures, mode, include, log)
        manifest = None
        if failures:
            output.mkdir(parents=True, exist_ok=True)
            _write_manifest(output, report_path, mode, include, [], {}, not_selected, failures, [])
            manifest = str(output / "manifest.json")
            log(f"清单：{manifest}")
        return summary(manifest=manifest)

    output.mkdir(parents=True, exist_ok=True)
    export_failures, done = [], set()
    for e in entries:
        rel = target_of[e["name"]]
        if rel in done:
            continue
        done.add(rel)
        try:
            _materialize(source_of[rel], output / rel, mode)
            log(f"已{mode}：{source_of[rel]} → {output / rel}")
        except Exception as exc:
            export_failures.append({"target": rel, "failure": f"{type(exc).__name__}: {exc}"})

    _write_manifest(output, report_path, mode, include, entries, target_of,
                    not_selected, failures, export_failures)
    if mode == "copy" and not export_failures:
        _write_dataset_yaml(output, entries, target_of)

    verification = None
    if not export_failures:
        try:
            read_ids = _verify(output, [e["episode_id"] for e in entries], profile)
            expected = sorted(e["episode_id"] for e in entries)
            verification = {"expected": expected, "read": sorted(read_ids),
                            "ok": sorted(read_ids) == expected}
            log(f"重新读取验证：读到 {len(read_ids)} 条，集合{'一致' if verification['ok'] else '不一致'}。")
        except Exception as exc:
            verification = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            log(f"重新读取验证失败：{verification['error']}")

    result = summary(exported=len(entries) - len(export_failures),
                     manifest=str(output / "manifest.json"),
                     verification=verification,
                     failures=failures + export_failures)
    log(f"清单：{result['manifest']}")
    log(f"完成：选择 {len(entries)} 条，导出 {result['exported']} 条，失败 {result['failed']} 条，未选择 {result['not_selected']} 条。")
    return result
