# Built-in Quality Algorithm Reference

[Home](../../README.md) · [简体中文](ALGORITHMS.zh-CN.md) · [Recommend, select, and run](../how-to/RUN_AND_SELECT.md)

This page describes the **inputs, parameters, outputs, and limits** of the nine built-in quality algorithms. It is for lookup and does not cover running them.
To inspect inputs, choose algorithms, execute them, and read the report, see [Run Algorithms](../how-to/RUN_AND_SELECT.md); output fields are in the [output objects and interface reference](OUTPUTS.md).

Installed metadata and source define all parameters; example thresholds are not universal device calibration. Items are PASS, REVIEW, SALVAGE, or EXCLUDE; inapplicability and execution failure are distinct from quality judgments.

<a id="structure-check"></a>
## Structure_Check

- **Input:** Channel catalog, declared camera requirements, duration, and robot parts.
- **Parameters:** Card `data.min_duration_s` defaults to 3 seconds; configuration defines required content.
- **Output/rules:** Structure score, missing content, and duration findings. Required missing content or short episodes affect decisions; very short episodes may block subsequent checks.
- **Limits:** Checks structural conditions: channels, cameras, and duration.
- [Implementation](../../src/edc/algorithms/quality/algorithms/Structure_Check/)

<a id="numeric-check"></a>
## Numeric_Check

- **Input:** Declared numeric fields; physical checks additionally need confirmed semantics, SI units, calibration, and thresholds.
- **Parameters:** `freeze_min_duration_s` defaults to 5. Cards support `range`; joint limits and speed bounds come only from the URDF, and without a URDF they are not checked and the report says so; `joint_stuck` needs joint and end-effector feedback from the same arm.
- **Output/rules:** Per-field finiteness, limit, and freeze findings. Nonfinite/out-of-range values can exclude data; freezes require review. Stuck joints with moving end effectors can produce a salvage item and remedy.
- **Limits:** Physical checks use configured units, calibration, and thresholds.
- [Implementation](../../src/edc/algorithms/quality/algorithms/Numeric_Check/)

<a id="action-smoothness"></a>
## Action_Smoothness

- **Input:** Selected arms' end-effector XYZ feedback in metres, measured or from explicit URDF forward kinematics, with usable channel time.
- **Method:** Split by the channel's own timeline, use each segment's median interval for a uniform grid, and extract holding vibration acceleration in frequency space. The head camera is not a default reference; this is not a fixed-frame jerk method.
- **Parameters:** `band_hz: [2, 10]`, `band_rate_ratio: 0.4`, `min_rate_hz: 10`, `still_speed_m_s: 0.01`, `still_min_s: 0.3`, `still_review_m_s2: 1.0`.
- **Output/rules:** Per-arm holding RMS acceleration (m/s²), events, and frozen readings; PASS, REVIEW, or not applicable. Movement values are informational, not exclusion criteria.
- **Limits:** Vibration checks require valid time and holding periods. Sample rate limits the band; thresholds need application calibration.
- [Method and full parameters, Chinese](../../src/edc/algorithms/quality/algorithms/Action_Smoothness/README.md)

<a id="check-timestamp"></a>
## Check_Timestamp

- **Input:** Valid time values with known units; cross-channel pairing additionally requires comparable clocks and reference inputs.
- **Parameters:** `shared_row_max_gap_s` defaults to three times the median row interval; scoring is the same for every robot: the error floor is 1/3 of the fastest color camera's frame interval, and interval variation is penalized from 0.3 and scores 0 at 0.6.
- **Output/rules:** Intervals, gaps, and available pairing metrics. Single/shared-row timeline checks do not establish cross-device synchronization. When a channel's time span (acquisition time) overlaps the reference camera's by less than 95%, it loses 2 points per missing percentage point; only no overlap at all scores 0. The reference (head) camera is not compared with itself; it gets its own row scored on cadence only, counted in the mean only when it is below the other rows' mean (it can lower the score, never raise it). If its time goes backward, repeats, or has no usable period, it cannot serve as the reference and the whole item scores 0 (exclude).
- **Limits:** Cross-channel checks require independent timestamps, known units, and comparable clocks.
- [Implementation](../../src/edc/algorithms/quality/algorithms/Check_Timestamp/)

<a id="sync-check"></a>
## Sync_Check

- **Input:** Independent valid acquisition timestamps with common clocks or explicit mappings; ROS/MCAP also use each message's record time. A derived view shares its source timeline and is counted once.
- **Parameters:** None (no `primary` or `weights`).
- **Output/rules:** Two items, no score. (1) Start/end overlap, by record time (acquisition time only when record time is missing): overlap below 80% of the span excludes; below 95%, or a channel starting late / ending early by more than 5 s, needs review and names the seconds. (2) Per-channel clock (its own record − header lag): groups over 1 s apart mean unsynchronised clocks, salvageable if fixed, excluded if moving; one channel's lag changing over 100 ms needs review (drift or jump). Without record time, item (2) is not assessed.
- **Limits:** Requires independent acquisition times; record delays use the 1 s / 100 ms thresholds. See Check_Timestamp for frame alignment.
- [Implementation](../../src/edc/algorithms/quality/algorithms/Sync_Check/)

<a id="root-cause"></a>
## Root_Cause

- **Input:** Comparable multi-channel timelines and source groups.
- **Parameters:** `families` declares groups of related sources.
- **Output/rules:** Three items, each naming the seconds. (1) Simultaneous gaps (at least 3 sources within 100 ms, one over 0.5 s): whole-system stall, excluded. (2) Single-channel gap (one channel over 0.5 s while other sources continue): that device's problem, needs review. (3) Periodic drops: excluded. Clock drift and jumps are checked by Sync_Check from each channel's record − header lag, not here (2026-11-04).
- **Limits:** Requires comparable timelines from multiple sources; results help locate anomalies.
- [Implementation](../../src/edc/algorithms/quality/algorithms/Root_Cause/)

The three timing methods share optional `record_header_deviation_threshold_ms` evidence. It defaults to `null` and does not automatically change scores. [Detailed evidence configuration, Chinese](EVIDENCE_AND_SCOPE.en.md)

<a id="dedup-check"></a>
## Dedup_Check

- **Input:** Recorded time values or supported sequence information; shared time is checked once with source references retained.
- **Parameters:** Declared channel selection; image-duplication parameters do not belong here.
- **Output/rules:** Repetition counts, consecutive runs, and existing duplicate scores.
- **Limits:** Checks repeated timestamps or sequence numbers; content duplication requires a separate check.
- [Implementation](../../src/edc/algorithms/quality/algorithms/Dedup_Check/)

<a id="reset-detection"></a>
## Reset_Detection

- **Input:** Selected feedback positions, matching Home references, and positive tolerances.
- **Parameters:** Card `home` uses joint radians or end-effector metres; both arms use the same card reference type and tolerance. Complete profiles also support explicit per-channel calibration. Fill `home` only with a pose the collection team has confirmed; otherwise, or for clips cut from longer recordings, use `checks.skip: [Reset_Detection]`.
- **Output/rules:** Start/end deviations from references and reset decisions; out-of-tolerance conditions require review.
- **Limits:** Home must match joint count, units, zero positions, and frames, and suit the task’s initial and final states.
- [Implementation](../../src/edc/algorithms/quality/algorithms/Reset_Detection/)

<a id="video-integrity"></a>
## Video_Integrity

- **Input:** Decodable frames/videos from selected wrist and explicitly shared cameras.
- **Parameters:** `input_mode`: `auto`/`frames`/`video`; `sample_step` defaults to 5; `downscale` controls scale; `freeze_motion_review` defaults to an empty mapping.
- **Output/rules:** Black/white, blurred, abnormal, or frozen-frame candidates and counts. Current content rules can exclude when a category affects at least 30% of sampled frames; lower proportions can pass with counts. Scores and manual-review evidence may coexist.
- **Limits:** Evaluates sampled frames; container checks apply to video only. Joint motion evidence supports manual review. [Joint evidence parameters](EVIDENCE_AND_SCOPE.en.md)
- [Implementation](../../src/edc/algorithms/quality/algorithms/Video_Integrity/)

## Related pages

- [Run Algorithms](../how-to/RUN_AND_SELECT.md): preflight, execution, and reading the report.
- [Output objects and interface reference](OUTPUTS.md): `Item`, `AlgorithmResult`, and run records.
- [Data formats and robot configuration reference](DATA_AND_ROBOTS.md): fields, units, and time declarations these algorithms rely on.
- [Scope and evidence reference](EVIDENCE_AND_SCOPE.en.md): arm scope, record-header evidence, and joint freeze evidence.
