# Algorithm Guide

[Home](../README.md) · [简体中文](ALGORITHMS.zh-CN.md) · [Data configuration](DATA_AND_CONFIGURATION.md)

<a id="selection"></a>
## Recommend, select, and execute

Recommendations use inputs, unit eligibility, semantics, time, parameters, and dependencies. `available` means preflight requirements are met; `partial` identifies a runnable subset and missing scope; `unavailable` gives missing conditions. Preflight neither reads every payload nor certifies data quality.

```bash
python examples/quickstart/recommend.py /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml
```

The script prints the full `edc.recommend()` JSON. Each episode's `algorithms` includes `algorithm.id`, `availability`, `reason`, `channel_bindings`, and `parameters`; multi-instance methods also retain `instances`.

Select two algorithms:

```bash
edc run /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --algorithm Structure_Check --algorithm Numeric_Check \
  --output /path/to/results/selected
```

`run` preserves explicit order. Default execution attempts full and partial quality methods and records unavailable reasons. `batch` runs the registered quality collection for each episode and can parallelize episodes; it has no `--algorithm` option:

```bash
edc batch /path/to/dataset \
  --robot /path/to/robot.yaml --output /path/to/results/report.html \
  --keep /path/to/results/details --jobs 1
```

`--keep` retains per-episode outputs and evidence in a new directory. `--jobs` controls episode concurrency (default 16; start with 1); `--tmp` sets temporary storage. `edc algorithms` lists registered algorithms. There is no standalone recommendation CLI or `edc config export` command.

### Python API

Pass the same effective parameters to recommendation and execution. Compile a robot card; read a complete profile directly:

```python
from pathlib import Path
import yaml
import edc

robot = Path('configs/robots/x1pro_mcap.yaml').resolve()
config = yaml.safe_load(robot.read_text(encoding='utf-8'))
if 'robot' in config and 'schema_version' not in config:
    profile, params, _ = edc.compile_card(config, base=robot.parent)
else:
    profile, params = config, {}

with edc.open_dataset('/path/to/dataset/episode.mcap', profile=profile) as dataset:
    recommendation = edc.recommend(dataset, params=params)
    selected = ['Structure_Check', 'Numeric_Check']
    result = edc.run(dataset, output='/path/to/results/api',
                     algorithm_ids=selected,
                     params={k: v for k, v in params.items() if k in selected})
```

Card parameters belong only under `checks.<algorithm_id>`; no separate parameter file overrides them. After the yaml changes, `edc batch` asks for a new signature. Use `arms: [left]` and `channels` according to each method's schema; rerun recommendations with the same updated scope. [Arm configuration](DATA_AND_CONFIGURATION.md#robot)

## Nine built-in quality algorithms

The following describes current inputs and primary behavior; implementation links provide full details. [Output representation](DEVELOPMENT.md#outputs) is shared. Installed metadata and source define parameters; example thresholds are not universal device calibration.

<a id="structure-check"></a>
### Structure_Check

- **Input:** Channel catalog, declared camera requirements, duration, and robot parts.
- **Parameters:** Card `data.min_duration_s` defaults to 3 seconds; configuration defines required content.
- **Output/rules:** Structure score, missing content, and duration findings. Required missing content or short episodes affect decisions; very short episodes may block subsequent checks.
- **Limits:** Structure does not establish physical semantics, task success, or sensor health. Missing camera information differs from a missing camera.
- [Implementation](../src/edc/algorithms/quality/algorithms/Structure_Check/)

<a id="numeric-check"></a>
### Numeric_Check

- **Input:** Declared numeric fields; physical checks additionally need confirmed semantics, SI units, calibration, and thresholds.
- **Parameters:** `freeze_min_duration_s` defaults to 5. Cards support `range`; joint limits and speed bounds come only from the URDF, and without a URDF they are not checked and the report says so; `joint_stuck` needs joint and end-effector feedback from the same arm.
- **Output/rules:** Per-field finiteness, limit, and freeze findings. Nonfinite/out-of-range values can exclude data; freezes require review. Stuck joints with moving end effectors can produce a salvage item and remedy.
- **Limits:** Unconfigured physical checks are not passes. Integrated IMU checks belong here; unknown effort is not inferred to be torque, and commands are not feedback.
- [Implementation](../src/edc/algorithms/quality/algorithms/Numeric_Check/)

<a id="action-smoothness"></a>
### Action_Smoothness

- **Input:** Selected arms' end-effector XYZ feedback in metres, measured or from explicit URDF forward kinematics, with usable channel time.
- **Method:** Split by the channel's own timeline, use each segment's median interval for a uniform grid, and extract holding vibration acceleration in frequency space. The head camera is not a default reference; this is not a fixed-frame jerk method.
- **Parameters:** `band_hz: [2, 10]`, `band_rate_ratio: 0.4`, `min_rate_hz: 10`, `still_speed_m_s: 0.01`, `still_min_s: 0.3`, `still_review_m_s2: 1.0`.
- **Output/rules:** Per-arm holding RMS acceleration (m/s²), events, and frozen readings; PASS, REVIEW, or not applicable. Movement values are informational, not exclusion criteria.
- **Limits:** Sample rate limits the band; review thresholds need target-domain calibration. Missing time units, insufficient sampling, or absent holding periods limit evaluation.
- [Method and full parameters, Chinese](../src/edc/algorithms/quality/algorithms/Action_Smoothness/README.md)

<a id="check-timestamp"></a>
### Check_Timestamp

- **Input:** Valid time values with known units; cross-channel pairing additionally requires comparable clocks and reference inputs.
- **Parameters:** `shared_row_max_gap_s` defaults to three times the median row interval; scoring is the same for every robot: the error floor is 1/3 of the fastest color camera's frame interval, and interval variation is penalized from 0.3 and scores 0 at 0.6.
- **Output/rules:** Intervals, gaps, and available pairing metrics. Single/shared-row timeline checks do not establish cross-device synchronization.
- **Limits:** Missing independent time, units, or clocks produces an explicitly limited recommendation.
- [Implementation](../src/edc/algorithms/quality/algorithms/Check_Timestamp/)

<a id="sync-check"></a>
### Sync_Check

- **Input:** Independent valid acquisition timestamps with common clocks or explicit mappings; ROS/MCAP also use each message's record time. A derived view shares its source timeline and is counted once.
- **Parameters:** None (no `primary` or `weights`).
- **Output/rules:** Two items, no score. (1) Start/end overlap, by record time (acquisition time only when record time is missing): overlap below 80% of the span excludes; below 95%, or a channel starting late / ending early by more than 5 s, needs review and names the seconds. (2) Per-channel clock (its own record − header lag): groups over 1 s apart mean unsynchronised clocks, salvageable if fixed, excluded if moving; one channel's lag changing over 100 ms needs review (drift or jump). Without record time, item (2) is not assessed.
- **Limits:** One shared row timeline cannot verify sensor synchronization. Record time includes transport latency, so differences under 1 s / 100 ms are not treated as clock faults. Frame-level cross-channel alignment belongs to Check_Timestamp and is not repeated here (user decision, 2026-11-04).
- [Implementation](../src/edc/algorithms/quality/algorithms/Sync_Check/)

<a id="root-cause"></a>
### Root_Cause

- **Input:** Comparable multi-channel timelines and source groups.
- **Parameters:** `families` declares groups of related sources.
- **Output/rules:** Coincident gaps and correlated timing anomalies, producing review findings under existing rules.
- **Limits:** Attribution provides investigation clues, not a definitive hardware diagnosis. A single shared timeline is insufficient.
- [Implementation](../src/edc/algorithms/quality/algorithms/Root_Cause/)

The three timing methods share optional `record_header_deviation_threshold_ms` evidence. It defaults to `null` and does not automatically change scores. [Detailed evidence configuration, Chinese](EVIDENCE_AND_SCOPE.md)

<a id="dedup-check"></a>
### Dedup_Check

- **Input:** Recorded time values or supported sequence information; shared time is checked once with source references retained.
- **Parameters:** Declared channel selection; image-duplication parameters do not belong here.
- **Output/rules:** Repetition counts, consecutive runs, and existing duplicate scores.
- **Limits:** Equal timestamps do not establish duplicated image/action content. Missing time is not a successful check.
- [Implementation](../src/edc/algorithms/quality/algorithms/Dedup_Check/)

<a id="reset-detection"></a>
### Reset_Detection

- **Input:** Selected feedback positions, matching Home references, and positive tolerances.
- **Parameters:** Card `home` uses joint radians or end-effector metres; both arms use the same card reference type and tolerance. Complete profiles also support explicit per-channel calibration. Fill `home` only with a pose the collection team has confirmed; otherwise, or for clips cut from longer recordings, use `checks.skip: [Reset_Detection]`.
- **Output/rules:** Start/end deviations from references and reset decisions; out-of-tolerance conditions require review.
- **Limits:** Do not transfer Home across incompatible joint counts, units, zero positions, or coordinate frames. Tasks with different valid initial/final states need appropriate scope.
- [Implementation](../src/edc/algorithms/quality/algorithms/Reset_Detection/)

<a id="video-integrity"></a>
### Video_Integrity

- **Input:** Decodable frames/videos from selected wrist and explicitly shared cameras.
- **Parameters:** `input_mode`: `auto`/`frames`/`video`; `sample_step` defaults to 5; `downscale` controls scale; `freeze_motion_review` defaults to an empty mapping.
- **Output/rules:** Black/white, blurred, abnormal, or frozen-frame candidates and counts. Current content rules can exclude when a category affects at least 30% of sampled frames; lower proportions can pass with counts. Scores and manual-review evidence may coexist.
- **Limits:** Images have no container metrics; sampled and full-frame inspections differ. Joint freeze review requires configured feedback and common clocks/explicit mappings; it adds evidence without automatic rescoring. [Joint evidence, Chinese](EVIDENCE_AND_SCOPE.md)
- [Implementation](../src/edc/algorithms/quality/algorithms/Video_Integrity/)

<a id="results"></a>
## Reading results

HTML provides an overall decision, episode overview, problems by algorithm, and episode details. Locate the affected episode and method, then inspect its reason. Full metrics and evidence are in JSON and outputs retained with `--keep`. See the [real MCAP reference result](QUICKSTART.md#results).

Item verdicts are PASS, REVIEW, SALVAGE, or EXCLUDE. Inapplicability and execution failure are distinct from quality judgments. A summary decision does not replace complete evaluation or workflow release conditions; unchecked inputs are not passes.

`run` exposes configuration, `records.jsonl`, `quality.json`, errors, and evidence under `_runs/<run_id>` through `catalog.json`. Native method metrics remain separate, with no combined algorithm/arm score. [Output objects](DEVELOPMENT.md#outputs)

Existing [copy/selection](SELECTION.md) and [workflow interfaces, Chinese](WORKFLOWS.md) remain available. Value, editing, and augmentation algorithms are integrated separately.
