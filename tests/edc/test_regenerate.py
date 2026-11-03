"""根据质检 JSON 报告筛选并重生成数据集（copy / link，保留原结论）。"""
import json

import pytest

from edc.data.regenerate import regenerate, RegenerateError


def episode_dir(parent, name, rows=3):
    """最小 episode 目录：轨迹 JSON + 占位 mp4，够 `detect_format` 识别即可（不读帧）。"""
    d = parent / name
    d.mkdir(parents=True)
    (d / "traj.json").write_text(json.dumps({"fps": 30, "data": [
        {"timestamp": i / 30, "follow_left_joint_position": [0.0] * 7} for i in range(rows)]}))
    (d / "cam.mp4").write_bytes(b"\x00" * 8)
    return d


def report_file(tmp_path, results):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"info": {}, "results": results}))
    return p


def result(name, decision, source):
    return {"name": name, "decision": decision, "source": str(source)}


def test_parse_source():
    from edc.data.regenerate import _parse_source
    assert _parse_source("/d/a.mcap") == ("/d/a.mcap", None)
    assert _parse_source("/d/a.mcap 第 2 条") == ("/d/a.mcap", 2)
    assert _parse_source("/d/带 空格 的目录/第 3 集.mcap 第 0 条") == ("/d/带 空格 的目录/第 3 集.mcap", 0)
    assert _parse_source("") == (None, None)
    assert _parse_source(None) == (None, None)


def read_ids(out):
    from edc.data.ir.canonical import open_dataset
    with open_dataset(str(out)) as ds:
        return [e.id for e in ds.iter_episodes()]


def test_copy_mode_default_keep_writes_manifest_and_dataset_yaml(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    good, bad, review = (episode_dir(data, n) for n in ("good_ep", "bad_ep", "review_ep"))
    rep = report_file(tmp_path, [result("good_ep", "keep", good),
                                 result("bad_ep", "exclude", bad),
                                 result("review_ep", "review", review)])
    out = tmp_path / "out"
    summary = regenerate(rep, out, log=lambda *a: None)
    assert summary["selected"] == 1 and summary["exported"] == 1 and summary["failed"] == 0
    assert (out / "manifest.json").is_file() and (out / "dataset.yaml").is_file()
    assert (out / "good_ep").is_dir()
    assert not (out / "bad_ep").exists() and not (out / "review_ep").exists()
    m = json.loads((out / "manifest.json").read_text())
    assert m["schema_version"] == "dqs_regenerate_manifest_v1"
    assert m["report_source"] == str(rep)
    assert m["filter"] == {"include": ["keep"], "mode": "copy"}
    assert m["episodes"] == [{
        "name": "good_ep", "original_decision": "keep", "source": str(good),
        "target": "good_ep", "mode": "copy", "episode_id": "good_ep",
        "format": "episode", "selector": {}, "status": "exported", "failure": None}]
    assert summary["verification"]["ok"] is True
    assert read_ids(out) == ["good_ep"]


def test_include_other_statuses_preserves_original_decision(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    keep, salvage, review = (episode_dir(data, n) for n in ("keep_ep", "salvage_ep", "review_ep"))
    rep = report_file(tmp_path, [result("keep_ep", "keep", keep),
                                 result("salvage_ep", "salvage", salvage),
                                 result("review_ep", "review", review)])
    out = tmp_path / "out"
    regenerate(rep, out, include=("keep", "salvage", "review"), log=lambda *a: None)
    m = json.loads((out / "manifest.json").read_text())
    decisions = {e["name"]: e["original_decision"] for e in m["episodes"]}
    # 原结论原样保留，绝不改写为“通过”。
    assert decisions == {"keep_ep": "keep", "salvage_ep": "salvage", "review_ep": "review"}
    assert sorted(read_ids(out)) == ["keep_ep", "review_ep", "salvage_ep"]


def test_link_mode_symlinks_and_rerereads_without_dataset_yaml(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    good, bad = episode_dir(data, "good_ep"), episode_dir(data, "bad_ep")
    rep = report_file(tmp_path, [result("good_ep", "keep", good), result("bad_ep", "exclude", bad)])
    out = tmp_path / "out"
    regenerate(rep, out, mode="link", log=lambda *a: None)
    assert (out / "good_ep").is_symlink()
    assert not (out / "dataset.yaml").exists()
    assert read_ids(out) == ["good_ep"]


def test_zero_match_is_clearly_reported_and_writes_nothing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ep = episode_dir(data, "bad_ep")
    rep = report_file(tmp_path, [result("bad_ep", "exclude", ep)])
    out = tmp_path / "out"
    summary = regenerate(rep, out, log=lambda *a: None)
    assert summary["selected"] == 0
    assert not out.exists()


def test_nonempty_output_is_rejected(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ep = episode_dir(data, "good_ep")
    rep = report_file(tmp_path, [result("good_ep", "keep", ep)])
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("occupied")
    with pytest.raises(RegenerateError, match="非空"):
        regenerate(rep, out, log=lambda *a: None)


def test_missing_source_is_recorded_as_failure(tmp_path):
    missing = tmp_path / "data" / "gone"
    rep = report_file(tmp_path, [result("gone", "keep", missing)])
    out = tmp_path / "out"
    summary = regenerate(rep, out, log=lambda *a: None)
    assert summary["selected"] == 0 and summary["failed"] == 1
    m = json.loads((out / "manifest.json").read_text())
    assert m["failed"][0]["name"] == "gone" and "源路径不存在" in m["failed"][0]["failure"]


def test_name_conflict_is_rejected(tmp_path):
    a = episode_dir(tmp_path / "d1", "same")
    b = episode_dir(tmp_path / "d2", "same")
    rep = report_file(tmp_path, [result("same", "keep", a), result("same", "keep", b)])
    with pytest.raises(RegenerateError, match="同名冲突"):
        regenerate(rep, tmp_path / "out", log=lambda *a: None)


def test_reserved_name_is_recorded_as_failure(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ep = episode_dir(data, "dataset.yaml")
    rep = report_file(tmp_path, [result("dataset.yaml", "keep", ep)])
    summary = regenerate(rep, tmp_path / "out", log=lambda *a: None)
    assert summary["failed"] == 1
    assert "保留文件冲突" in summary["failures"][0]["failure"]


def test_unknown_decision_kept_literal_when_explicitly_included(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ep = episode_dir(data, "weird")
    rep = report_file(tmp_path, [result("weird", "unassessed", ep)])
    out = tmp_path / "out"
    regenerate(rep, out, include=("unassessed",), log=lambda *a: None)
    m = json.loads((out / "manifest.json").read_text())
    assert m["episodes"][0]["original_decision"] == "unassessed"


def test_folder_of_independent_episodes_is_rejected_with_boundary(tmp_path):
    # 一个目录里有多条独立 episode（子目录），报告没写第几条 → 记失败并说明边界。
    parent = tmp_path / "ds"
    episode_dir(parent, "a")
    episode_dir(parent, "b")
    rep = report_file(tmp_path, [result("a", "keep", parent)])
    summary = regenerate(rep, tmp_path / "out", log=lambda *a: None)
    assert summary["selected"] == 0 and summary["failed"] == 1
    assert "第几条" in summary["failures"][0]["failure"]


def test_output_must_not_live_inside_a_source(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ep = episode_dir(data, "good_ep")
    rep = report_file(tmp_path, [result("good_ep", "keep", ep)])
    # 输出目录放进源目录里，会递归复制；必须拒绝。
    with pytest.raises(RegenerateError, match="不能位于源数据内"):
        regenerate(rep, ep / "out", log=lambda *a: None)
