# Embodied Data Curation Framework

A framework for selecting data checks by declared inputs, inspecting evidence, and tracing derived embodied datasets.

[中文文档](README.zh-CN.md) · [Workflow guide](docs/framework/WORKFLOWS.md) · [Contributing](CONTRIBUTING.md) · [Verified status](docs/framework/STATUS.md)

The first delivery contains a shared data core and **nine CPU quality algorithms**. Quality, value, editing and augmentation are separate plugin categories. The default workflow is **quality → value → optional augmentation**. Editing produces a new data version and requires quality checks again. A finite configuration controls all stages; there is no automatic repair loop.

The existing `embodied-dqs` package and `dqs` Python/CLI remain compatible. `dqs` retains the historical abbreviation **Data Quality Service** and now exposes the wider curation workflow.

## Install and try

Python ≥3.10. The base package requires only NumPy and PyYAML. Additional readers, image inspection and scientific methods use optional dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[formats]'
dqs demo fetch synthetic --output /tmp/curation-demo
dqs recommend /tmp/curation-demo
dqs run /tmp/curation-demo --algorithm Numeric_Check --output /tmp/curation-results
```

The deterministic demo is synthetic and deliberately has unconfirmed requirements. Successful execution does not mean every quality requirement passed.

```bash
pip install -e examples/plugins/algorithm
python examples/workflows/make_example.py --output /tmp/curation-workflow
dqs workflow check /tmp/curation-workflow/workflow.yaml
dqs workflow run /tmp/curation-workflow/workflow.yaml --output /tmp/curation-chain
dqs workflow run /tmp/curation-workflow/workflow.yaml --output /tmp/curation-chain --resume
```

This synthetic workflow inspects two episodes, creates edited copies, rechecks them, inventories the current collection once, creates four augmented episodes, and rechecks those copies. Inventory is **not valuation**; the brightness transform is **not validated repair or training benefit**.

## Contracts

- Dataset → Episode → Channel retains stable IDs, explicit boundaries, selectors and provenance.
- Dataset configuration declares storage and versions; robot configuration declares logical channels and semantics; workflow configuration selects algorithms and parameters.
- Physical algorithms consume SI execution views. Storage stays unchanged; unknown units or semantics block physical evaluation. Raw finite-value checks remain available.
- Acquisition, container, playback and synthetic time stay distinct. Cross-channel analysis requires a common clock or an explicit mapping. Mapping does not resample or establish clock truth.
- `AlgorithmSpec` + `run(context, params)` plugins use `dqs.algorithms`; reader plugins use `dqs.readers`. Custom IDs use `team__algorithm`.
- Execution status, data judgment and human review remain separate. No cross-algorithm aggregate score is generated.
- Editing and augmentation return output, parameters and source mappings. The initial validator supports identity row mappings, unchanged shapes and explicit changed channels.
- Immutable `context.upstream` supplies exact run references, source fingerprints and raw upstream results. Dataset plugins run once per selected collection.
- `catalog.json` discovers `_runs`, fixed reports, and versioned `_workflows`. Review is append-only; resume checks inputs, configuration, code, environment and evidence.

## CPU methods

`Structure_Check`, `Numeric_Check`, `Action_Smoothness`, `Check_Timestamp`, `Sync_Check`, `Root_Cause`, `Dedup_Check`, `Reset_Detection`, `Video_Integrity`.

All support explicit channel selection. Original methods, scoring formulas and thresholds are retained. Image/frame inspection and video/container inspection have separate coverage. See the [Chinese algorithm table](README.zh-CN.md#cpu-质量算法) for minimum inputs and limitations.

Readers include native HDF5, Parquet, MCAP, ROS bags, JSON/episode, MP4 and LeRobot. Reader availability is distinct from validated robot support. Official LeRobot is optional and version-pinned; see [LeRobot support](docs/framework/LEROBOT.md).

## Layout and roadmap

`dqs/` exposes thin public APIs; `framework/` contains readers, robots, transforms, conversion, execution and workflows; `Quality_Assessment/` contains the nine CPU methods. `examples/`, `tests/`, `docs/` and CI support integration and review.

The core, CPU methods and synthetic workflow are the initial deliverable. Real value/editing/augmentation methods require independent input, environment, output and license validation before support is claimed. [Build plan](docs/framework/BUILD_PLAN.md) · [Contributor tasks](docs/framework/CONTRIBUTOR_TASKS.md).

## Distribution status

Private collaboration baseline. Repository-wide redistribution permission remains unresolved; no MIT/Apache license is asserted. Real data, model weights, private wheels, third-party submodules and historical experiments are excluded. See the [release checklist](docs/RELEASE_CHECKLIST.md) before any public release.
