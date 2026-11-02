# Embodied Data Curation Framework

**EDC (Embodied Data Curation)** provides robot data ingestion, input-based algorithm recommendations, CPU inspection, auditable plugin workflows, and evidence reports.

[简体中文](README.zh-CN.md) · [Configuration](docs/framework/CONFIGURATION.md) · [Algorithms](docs/framework/ALGORITHMS.md) · [Three-person collaboration](FRIEND_VALIDATION.md)

## News

- 2026-10-27 (0.2.0a2): automatic XYZW quaternion orientation reads; configurable 107-name channel registry with IMU, odometry, hand, gripper and tactile templates. [Contract and migration](docs/framework/ORIENTATION_HAND_TACTILE.md).

- 2026-10-26: Python/CLI `edc`, distribution `embodied-data-curation`, and `src/edc` module layout.
- Four reusable configuration components with standalone export; explicit single/dual arm scope; one robot across multiple task datasets.
- Editing and augmentation create validated copies and return to quality checks. See [verified status and limits](docs/framework/STATUS.md).

## Quick start: X1 Pro MCAP

The `data-platform` branch starts from `leju-kuavo` commit `87c5e6f`. This example reads a real MCAP directly, including embedded images and numeric signals. The source remains on the server; its redistribution license is unknown.

Use Python 3.10+ and run from this branch's repository root:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[formats]'
. .venv/bin/activate
```

The documentation uses the following path placeholders consistently. Replace them with actual values and pass the input through `--input`; no dataset YAML edit is needed:

| Placeholder | Meaning |
|---|---|
| `<DATASET_DIR>` | Absolute path to the dataset directory containing the MCAP |
| `<MCAP_FILE>` | MCAP filename including `.mcap`, without a directory |

The full input path is `<DATASET_DIR>/<MCAP_FILE>`; the batch command below uses the same placeholders.

The example configuration explicitly registers one Episode (`x1_pro_reference_0001`), retaining all 32 source channels. Keep the explicit ID and selector. The task is unconfirmed (`tasks: []`); directory names do not establish it.

```bash
MCAP_PATH='<DATASET_DIR>/<MCAP_FILE>'
edc config check configs/x1_pro.yaml --input "$MCAP_PATH"
edc config recommend configs/x1_pro.yaml --input "$MCAP_PATH"
edc config run configs/x1_pro.yaml --input "$MCAP_PATH" --output results/x1-pro-mcap
python -m http.server 8000 --directory results/x1-pro-mcap
```

All three commands use the same input path. `--input` overrides only this invocation, preserving the explicit Episode ID, format and selector; the effective path is saved in the run snapshot. Omitting it uses the path in `configs/datasets/x1_pro_mcap.yaml`. Relative input paths resolve from the command's working directory. For other captures, check the applicability of robot declarations, units and calibration. Register multiple explicit episodes in the dataset YAML, or use the batch command below.

Open [the report](http://localhost:8000/report.html). In VS Code Remote-SSH, forward port 8000 in the Ports panel, then append `/report.html` to the forwarded local address. Keep the whole output directory so evidence links work.

**One episode and many episodes share the batch report layout.** `config run`, `edc run` and `edc batch` show decision counts, an episode-by-algorithm table and findings grouped by algorithm. This example has one row; click its episode name to expand the original per-episode detail table. The layout matches the `leju-kuavo` reference: four verdict explanations and four main sections. Checks use the existing pass/review/salvage/exclude verdicts; checks without the conditions needed for a judgment display not applicable. Full records and evidence remain in `_runs`, indexed by `catalog.json`. The `report.html` path is unchanged. Refresh an existing output with `edc report results/x1-pro-mcap` without rerunning algorithms.

Expect a report with **delivery suspended**: all nine methods have status records, smoothness has separate left/right instances, and `Sync_Check` / `Root_Cause` explicitly run recording-time diagnostics; these do not establish acquisition synchronization or device root causes. Consequently `config run` returns exit code **1** for unmet release conditions; check individual records for actual processing failures. See the [measured acceptance results](docs/validation/x1_pro_mcap_20261102/README.md).

| File | Purpose |
|---|---|
| `configs/x1_pro.yaml` | Unified entry referencing four components |
| `configs/datasets/x1_pro_mcap.yaml` | Location, stable Episode ID, provenance and unknown task |
| `configs/robots/x1_pro.yaml` | Dual arms, 32 channel semantics, units, separate command/feedback limits, reset calibration and numeric confirmation |
| `configs/algorithms/x1_pro_mcap.yaml` | Head-frame reference, 0.1 s maximum interpolation span, physical field scope and image parameters |
| `configs/workflows/x1_pro_mcap.yaml` | Nine explicit CPU checks, including blocked methods |

Master joints are commands; follower joints are feedback. Arm positions and velocities are confirmed rad and rad/s. The master velocity/position derivative inconsistency remains a diagnostic; units rely on owner confirmation. Arm effort is current with unknown source units and never enters torque checks. Physical numeric checks cover the four arm position/velocity pairs and two follower XYZ fields (10 fields); other numeric fields retain finiteness diagnostics. The `data-platform-owner` attestation binds the thresholds and field scope. Changes require renewed owner review and confirmation.

Each head RGB frame's MCAP `log_time` supplies the intermediate reference. Follower XYZ is linearly interpolated on the file's container clock, using overlapping real head frames, no extrapolation, and a maximum source bracket of 0.1 s. Images use original embedded frames. **Device clock relationships remain unknown: container alignment cannot prove acquisition synchronization.** Smoothness, sync and root-cause checks retain their original scores and judgments for the selected recording-time input. Sync and root-cause checks select `timeline: {time_source: container}` to inspect recording coverage, relative timing stability and correlated gaps. Acquisition synchronization and device fault causes remain unassessed; recording anomalies alone do not establish acquisition faults. Nine records do not imply nine passes. Original acquisition/container nanoseconds, source rows and interpolation weights remain available.

`results/x1-pro-mcap/catalog.json` discovers outputs. `report.html` presents results; `_workflows/<id>/resolved-config.json` stores the workflow and effective configuration; `_runs/<id>/run.json` stores run parameters and versions. Algorithm directories contain `records.jsonl`, `errors.jsonl` and `artifacts/`; smoothness interpolation evidence is `Action_Smoothness/artifacts/<id>/timeline.json`; sync/root-cause recording timestamps and source rows are in their respective `artifacts/<id>/container_timeline.json`. Outputs never overwrite the source MCAP.

Optional standalone configuration export (use a new output file):

```bash
edc config export configs/x1_pro.yaml --input "$MCAP_PATH" --output results/x1-pro-resolved.yaml
```

## Batch inspection example (optional)

The quick start above already generates a report. To inspect one or more files with `edc batch`, replace the path placeholders in the following command with actual values. This command uses the supplied file paths, the same robot and the matching nine-method parameters in `configs/robots/x1_pro/params.json`. Supply explicit MCAP file paths: recursive directory discovery currently does not scan `.mcap`.

This example assumes **one complete trajectory (Episode) per MCAP file**, with embedded images and numeric signals; no companion JSON or MP4 files are required. A dataset directory can be organized as follows:

```text
<DATASET_DIR>/
├── <MCAP_FILE_1>    # e.g. episode_001.mcap
└── <MCAP_FILE_2>    # e.g. episode_002.mcap
```

Replace `<MCAP_FILE_1>` and `<MCAP_FILE_2>` with distinct `.mcap` filenames, following the same convention as `<MCAP_FILE>` above. Files may also reside in subdirectories; supply their corresponding paths in the command. Filenames must be unique across all inputs.

```bash
edc batch \
  '<DATASET_DIR>/<MCAP_FILE_1>' \
  '<DATASET_DIR>/<MCAP_FILE_2>' \
  --robot x1_pro --jobs 1 \
  --output results/x1-pro-batch.html --keep results/x1-pro-batch-runs
```

The batch HTML has a companion JSON summary. `--keep` preserves per-file reports and evidence and requires a new directory. Multiple explicit MCAP paths are accepted. Verify hardware, units, calibration and clock declarations before using another capture. Batch IDs follow file names; use unified configuration for custom stable Episode IDs.

The existing JSON + MP4 reader remains available through the separate `x1pro_episode` profile, whose clock declarations and reset tolerances differ from this MCAP profile. See the [JSON + MP4 configuration](configs/robots/x1pro_episode.yaml). For a synthetic SDK demonstration without real data, run `python examples/curation/make_example.py --output /tmp/edc-demo`.

## Contracts

Dataset → Episode → Channel preserves explicit boundaries, original values, sources, task metadata and time evidence. Confirmed physical inputs use SI; unknown semantics block physical computation. Selecting a reference camera does not establish a common clock.

`arms: all` selects arm parts explicitly declared by the robot. A neutral single arm uses `main`; dual arms use `left/right`. Missing declared arms remain missing. Algorithms preserve independent results without cross-arm or cross-method average scores.

The default workflow runs one round of runnable quality methods and can finish at its quality report. The base distribution contains repository-owned CPU methods and modality indicators; it has no external algorithm or model dependency. Value assessment is opt-in after the quality gate. Every editing or augmentation output creates a new version and must pass consistency validation and a quality recheck before delivery. Missing selected inputs, partial assessment, and execution failures cannot count as passing. Future GPU and value implementations require separate integration and acceptance.

All algorithms in the shipped pool are implemented and registered in this repository; the base distribution does not require an external algorithm package, model, private wheel, or GPU runtime. `AlgorithmSpec`/`run(context, params)` and the `edc.algorithms`/`edc.readers` entry-point groups remain development interfaces for adding future repository modules or explicitly installed integrations. They do not add algorithms to the shipped pool automatically. Production auto-selection excludes SDK examples, and real value/edit/augmentation methods require separate acceptance.

## Repository

`src/edc/{data,robots,algorithms,execution,workflows,reporting}` contains the runtime. `configs/{datasets,robots,algorithms,workflows}`, `examples`, `tests/edc`, and `docs` provide configuration, demonstrations and evidence. The base install requires only NumPy/PyYAML; format and model dependencies are optional.

CPU methods: Structure_Check, Numeric_Check, Action_Smoothness, Check_Timestamp, Sync_Check, Root_Cause, Dedup_Check, Reset_Detection, Video_Integrity.

The core pool currently contains 66 repository-owned algorithms: 9 default CPU methods and 57 modality indicators (46 implemented, 11 registered with missing implementation or input coverage). The three-person validation plan covers data configuration, algorithm inputs and workflow acceptance. Each round uses the same `main` commit; see [roles and deliverables](FRIEND_VALIDATION.md).

This repository is a private development delivery. Licensing and redistribution of code, algorithms, models and data must be verified before publication; see the [release checklist](docs/RELEASE_CHECKLIST.md). The persisted record protocol remains `dqs_record_v1` (DQS: Data Quality Service); the public interface is EDC. The default `quality_cpu` preset contains the original nine CPU methods, while the repository-owned `quality_indicators` preset exposes the modality indicator pool.
