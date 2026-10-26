import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import yaml
from edc import open_dataset, run
from edc.execution.examples import generate
from edc.algorithms.registry import AlgorithmSpec, Registry
from edc.algorithms.quality.adapters.cpu import preflight, builtins

@pytest.fixture
def demo(tmp_path):
    return generate(tmp_path / "demo")


def test_end_to_end_and_repeat_catalog(demo, tmp_path):
    output = tmp_path / "results"
    with open_dataset(demo) as ds:
        result = run(ds, output=output, algorithm_ids=["Numeric_Check", "Check_Timestamp"])
        assert result["status_counts"] == {"completed": 2, "missing_input": 2}
        run(ds, output=output, algorithm_ids=["Numeric_Check"])
    catalog = json.loads((output / "catalog.json").read_text())
    assert len(catalog["runs"]) == 2
    for entry in catalog["runs"]:
        for ref in entry["records"]:
            for line in (output / ref).read_text().splitlines():
                for artifact in json.loads(line)["artifacts"]:
                    assert (output / artifact).is_file()
    assert "Numeric_Check" in (output / "report.html").read_text()
    choices = json.loads((output / catalog["runs"][0]["selection"]).read_text())
    assert choices[0]["decision"] == "unassessed"


def test_bad_episode_continues(demo, tmp_path):
    (demo / "episodes/episode_001/trajectory.json").write_text("{")
    with open_dataset(demo) as ds:
        summary = run(ds, output=tmp_path / "out", algorithm_ids=["Numeric_Check"])
    assert summary["status_counts"] == {"processing_error": 1, "completed": 1}


def test_no_synthetic_sync_or_semantic_alias(demo):
    with open_dataset(demo) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            assert ep.channels["follow_right_joint_pos"].time_source == "synthetic"
            with pytest.raises(ValueError, match="Field not provided"):
                ep.read_signal("follow_right_joint_pos", "effort")
            plugin = next(p for p in builtins() if p.spec.id == "Sync_Check")
            assert preflight(plugin, ep)[0] == "missing_input"


def test_registry_rejects_duplicates_and_api_version():
    registry = Registry("edc.algorithms")
    plugin = SimpleNamespace(spec=AlgorithmSpec("team__algo", "1", "Test", "Team", "Method"), run=lambda c, p: {})
    registry.register(plugin)
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(plugin)
    bad = SimpleNamespace(spec=AlgorithmSpec("other", "1", "Test", "Team", "Method", interface_version=2))
    with pytest.raises(ValueError, match="Incompatible"):
        registry.register(bad)


def test_external_plugin_discovered_and_failure_isolated(monkeypatch, demo, tmp_path):
    def fail(ctx, params):
        raise RuntimeError("plugin failed")
    plugin = SimpleNamespace(spec=AlgorithmSpec("team__test", "1", "Test", "Team", "Method"), run=fail)
    point = SimpleNamespace(name="team__test", load=lambda: plugin)
    monkeypatch.setattr("edc.algorithms.registry.entry_points", lambda group: [point] if group == "edc.algorithms" else [])
    with open_dataset(demo) as ds:
        result = run(ds, output=tmp_path / "out", algorithm_ids=["team__test", "Numeric_Check"])
    assert result["status_counts"] == {"processing_error": 2, "completed": 2}


def test_profile_and_manifest_reject_ambiguous_or_unsafe(demo):
    manifest = yaml.safe_load((demo / "dataset.yaml").read_text())
    manifest["episodes"][1]["id"] = manifest["episodes"][0]["id"]
    (demo / "dataset.yaml").write_text(yaml.safe_dump(manifest))
    with pytest.raises(ValueError, match="unique"):
        open_dataset(demo)
    manifest["episodes"][1]["path"] = "../../outside"
    (demo / "dataset.yaml").write_text(yaml.safe_dump(manifest))
    with pytest.raises(ValueError, match="within"):
        open_dataset(demo)


def test_hdf5_group_identity_and_values(tmp_path):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "data.h5"
    with h5py.File(path, "w") as h:
        for i in range(2):
            group = h.create_group(f"episodes/ep{i}")
            group["timestamp"] = [0, .1, .2]
            group["joint_positions"] = np.ones((3, 6)) * i
    with open_dataset(path) as ds:
        episodes = list(ds.iter_episodes())
        assert len(episodes) == 2
        for i, ep in enumerate(episodes):
            with ep.opened():
                np.testing.assert_equal(ep.read_signal("joint_positions", "position"), np.ones((3, 6)) * i)


def test_parquet_episode_filtering(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"episode_index": [2, 1, 2], "timestamp": [0., 0., .1],
                             "joint_positions": [[2.], [1.], [3.]]}), path)
    with open_dataset(path) as ds:
        episodes = list(ds.iter_episodes())
        assert len(episodes) == 2
        with episodes[0].opened() as ep:
            np.testing.assert_equal(ep.read_signal("joint_positions", "position"), [[2.], [3.]])


@pytest.mark.parametrize("version", ["v2.1", "v3.0"])
def test_lerobot_shared_file_boundaries(tmp_path, version):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    (tmp_path / "meta").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "meta/info.json").write_text(json.dumps({"codebase_version": version}))
    rows = [{"episode_index": 0, "length": 2}, {"episode_index": 1, "length": 1}]
    if version == "v2.1":
        (tmp_path / "meta/episodes.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    else:
        (tmp_path / "meta/episodes").mkdir()
        pq.write_table(pa.Table.from_pylist(rows), tmp_path / "meta/episodes/file.parquet")
    pq.write_table(pa.table({"episode_index": [0, 0, 1], "timestamp": [0., .1, 0.],
                             "joint_positions": [[1.], [2.], [9.]]}), tmp_path / "data/shared.parquet")
    with open_dataset(tmp_path, backend='compat') as ds:
        assert len(list(ds.iter_episodes())) == 2
        with list(ds.iter_episodes())[1].opened() as ep:
            np.testing.assert_equal(ep.read_signal("joint_positions", "position"), [[9.]])


def test_missing_field_rows_raise(demo):
    path = demo / "episodes/episode_001/trajectory.json"
    content = json.loads(path.read_text())
    del content["data"][1]["follow_right_joint_pos"]
    path.write_text(json.dumps(content))
    with open_dataset(demo) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            with pytest.raises((ValueError, TypeError)):
                ep.read_signal("follow_right_joint_pos", "position")


def test_unknown_time_source_not_elevated(demo):
    path = demo / "episodes/episode_001/trajectory.json"
    content = json.loads(path.read_text())
    for i, row in enumerate(content["data"]):
        row["timestamp"] = i / 30
    path.write_text(json.dumps(content))
    with open_dataset(demo) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            plugin = next(p for p in builtins() if p.spec.id == "Check_Timestamp")
            assert preflight(plugin, ep)[0] == "missing_input"


def test_output_cannot_overwrite_source(demo):
    with open_dataset(demo) as ds:
        with pytest.raises(ValueError, match="episode source"):
            run(ds, output=demo / "episodes/episode_001/output")


def test_reset_requires_calibration(demo):
    with open_dataset(demo) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            plugin = next(p for p in builtins() if p.spec.id == "Reset_Detection")
            assert preflight(plugin, ep)[0] == "needs_calibration"


def test_current_is_not_torque(demo):
    path = demo / "episodes/episode_001/trajectory.json"
    content = json.loads(path.read_text())
    for row in content["data"]:
        row["follow_right_joint_cur"] = [1.] * 6
    path.write_text(json.dumps(content))
    with open_dataset(demo) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            assert ep.channels["follow_right_joint_cur"].fields == ["current"]
            with pytest.raises(ValueError):
                ep.read_signal("follow_right_joint_cur", "effort")


def test_processing_error_wrapped_by_legacy_stays_error(monkeypatch, demo, tmp_path):
    plugin = SimpleNamespace(spec=AlgorithmSpec("test_error", "1", "Test", "Team", "Method"),
        run=lambda c, p: {"verdict": "NEEDS_REVIEW", "ir_unavailable": {}, "reasons": ["RuntimeError: bad"]})
    registry = Registry("edc.algorithms")
    registry.register(plugin)
    monkeypatch.setattr("edc.execution.runner.algorithms", lambda: registry)
    with open_dataset(demo) as ds:
        result = run(ds, output=tmp_path / "out", algorithm_ids=["test_error"])
    assert result["status_counts"] == {"processing_error": 2}
    catalog = json.loads((tmp_path / "out/catalog.json").read_text())
    choices = json.loads((tmp_path / "out" / catalog["runs"][0]["selection"]).read_text())
    assert all(c["decision"] == "unassessed" for c in choices)


def test_robot_validation_dimensions(demo):
    from edc.robots.validation import check_robot
    with open_dataset(demo) as ds:
        assert check_robot(ds) == []
        ds.profile["channels"]["follow_right_joint_pos"]["dimensions"] = [7]
        assert any("dimension mismatch" in issue for issue in check_robot(ds))


def test_report_escapes_untrusted_text(monkeypatch, demo, tmp_path):
    plugin = SimpleNamespace(spec=AlgorithmSpec("test_html", "1", "Test", "Team", "Method"),
        run=lambda c, p: {"reason": "<script>alert(1)</script>", "verdict": "OBSERVATION"})
    registry = Registry("edc.algorithms")
    registry.register(plugin)
    monkeypatch.setattr("edc.execution.runner.algorithms", lambda: registry)
    with open_dataset(demo) as ds:
        run(ds, output=tmp_path / "out", algorithm_ids=["test_html"])
    page = (tmp_path / "out/report.html").read_text()
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_reader_entry_point(tmp_path, monkeypatch):
    from edc.data.ir.dataset import Dataset, Episode
    plugin = SimpleNamespace(spec=SimpleNamespace(id="team__reader", version="1", interface_version=1),
        can_open=lambda path: path == tmp_path,
        open_dataset=lambda path, profile: Dataset(path, profile, {"id": "external"}, [Episode("a", path, "episode", profile)]))
    point = SimpleNamespace(name="team__reader", load=lambda: plugin)
    monkeypatch.setattr("edc.algorithms.registry.entry_points", lambda group: [point] if group == "edc.readers" else [])
    assert open_dataset(tmp_path).id == "external"


def test_cpu_import_does_not_load_model_libraries():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c", "import edc, sys; assert not {'torch', 'transformers', 'tensorflow', 'jax'} & sys.modules.keys()"], capture_output=True)
    assert result.returncode == 0, result.stderr


def test_bad_hdf5_does_not_abort_discovery(demo, tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    import shutil
    shutil.copytree(demo / "episodes/episode_001", root / "good")
    (root / "broken.h5").write_bytes(b"not hdf5")
    with open_dataset(root) as ds:
        result = run(ds, output=tmp_path / "out", algorithm_ids=["Numeric_Check"])
    assert result["status_counts"] == {"processing_error": 1, "completed": 1}
