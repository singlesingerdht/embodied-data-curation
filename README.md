# Embodied Data Curation Framework

**EDC (Embodied Data Curation)** provides robot data ingestion, input-based algorithm recommendations, CPU inspection, auditable plugin workflows, and evidence reports.

[简体中文](README.zh-CN.md) · [Configuration](docs/framework/CONFIGURATION.md) · [Algorithms](docs/framework/ALGORITHMS.md) · [Three-person collaboration](FRIEND_VALIDATION.md)

## News

- 2026-10-27 (0.2.0a2): automatic XYZW quaternion orientation reads; configurable 107-name channel registry with IMU, odometry, hand, gripper and tactile templates. [Contract and migration](docs/framework/ORIENTATION_HAND_TACTILE.md).

- 2026-10-26: Python/CLI `edc`, distribution `embodied-data-curation`, and `src/edc` module layout.
- Four reusable configuration components with standalone export; explicit single/dual arm scope; one robot across multiple task datasets.
- Editing and augmentation create validated copies and return to quality checks. See [verified status and limits](docs/framework/STATUS.md).

## Quick start

```bash
pip install -e '.[formats,test]'
python examples/curation/make_example.py --output /tmp/edc-demo
edc config check /tmp/edc-demo/curation.yaml
edc config recommend /tmp/edc-demo/curation.yaml
edc config export /tmp/edc-demo/curation.yaml --output /tmp/edc-complete.yaml
edc config run /tmp/edc-complete.yaml --output /tmp/edc-results
edc config run /tmp/edc-complete.yaml --output /tmp/edc-results --resume
```

This is an explicitly synthetic SDK demonstration, not a robot repair, valuation, or training-benefit claim. Output starts at `catalog.json`, `report.html`, `_runs`, and `_workflows`.

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
