# Time-Difference Evidence, Arm Scope, and Joint Visual Review

[简体中文](EVIDENCE_AND_SCOPE.en.md)

This page lists configuration and evidence fields for time differences, arm scope, and joint visual review. These evidence features do not add standalone algorithms or change algorithm scores, physical thresholds, or output base classes.
For algorithm inputs and judgments, see the [built-in quality algorithm reference](ALGORITHMS.md); for stage handoffs and rechecks, see [quality workflows](../how-to/WORKFLOWS.en.md).

## Time algorithms: configuration and results

Set these parameters under `checks` in robot YAML. All three methods share the same parameter; workflow stage `params` can override it:

```yaml
checks:
  Check_Timestamp:
    record_header_deviation_threshold_ms: null
  Sync_Check:
    record_header_deviation_threshold_ms: 100
  Root_Cause:
    record_header_deviation_threshold_ms: null
```

- Default `null`: show the median difference, minimum/maximum difference, maximum absolute deviation, and corresponding messages, without adding an anomaly threshold.
- An explicit nonnegative millisecond value additionally lists the count, consecutive source-row intervals, and details for `abs(deviation) > threshold`. Details are limited to 20 entries; `details_truncated` indicates truncation.
- Actual difference = raw record time − raw header time; deviation = actual difference − the median difference over the full sequence. Subtract integer nanoseconds before converting to milliseconds, using raw values before clock mapping or automatic shifting.
- Each raw row represents one message. `source_row` starts at 0; `message_number` starts at 1. Derived views do not recount the same source message sequence. Pairs with missing/zero headers are excluded and counted as invalid. If either raw time type is absent, the difference is marked unavailable rather than substituting another time source.
- Differences are observations, not diagnoses of device or network faults. This evidence does not introduce automatic score deductions, review states, or release conditions.

JSON `record_header_differences` retains the full statistics; individual evidence entries use the existing `Item.evidence`. For example:

> /arm, message 151: time difference 500 ms (typical 20 ms, deviation +480 ms)

Batch HTML retains its four columns. Each algorithm cell shows its judgment, short note, and reasons for non-pass results; it adds no checked-item details, standalone time-difference table, scope description, or duplicate table for missing paired times.
Median values, difference ranges, source rows, thresholds, over-threshold messages, and the complete input scope remain in JSON. Rendering does not rewrite records, scores, or quality judgments.
Reports retain existing labels and judgment rules, without adding labels such as “undetermined” or “evaluation incomplete,” or artificial failure examples. Actual execution errors retain their reasons.

In a controlled synthetic ROS2 MCAP comparison, both the normal input and the input with one spike scored 99.844 for timestamps, 100 for synchronization, and 100 for root-cause analysis.
These values only show that the added evidence did not change scores for that synthetic input; they are neither real-robot validation nor performance metrics.
A fixed 2 s difference is still reported by existing logic; its added deviation is 0, so a stable offset is not presented as a local spike.
See the [scope result example](../examples/arm_scope_after.json).

## Arm scope: data declarations and algorithm selection

Robot YAML `arms` declares actual arms and field sources; `checks.<method>.arms` selects the scope of a check.
Use `main` for a standalone arm. Dual-arm checks default to `all`; `[left]`/`[right]` select declared component IDs rather than guessing from topic names.

```yaml
arms:
  left:
    joints: {source: combined_joints, index: 0-5, unit: rad}
  right:
    joints: {source: combined_joints, index: 6-11, unit: rad}
checks:
  Numeric_Check:
    arms: [left]
```

This example checks only the first 6 joints; NaNs in the last 6 do not enter the left-arm result. For separately stored arms, set each arm's `source`; behavior is the same.
Explicit selection from a shared source array still expands declared component views. Covered source fields are not checked again as a whole array.
For source fields only partially covered by views, retain diagnostics only for uncovered fields with known ownership. When selecting one arm, shared source fields without declared components are skipped with a request to complete the configuration. Numeric inputs with unknown ownership are not automatically treated as shared sensors.
Configuration and `select` source associations determine component relationships. Grippers, hands, and end-effector poses follow their owning arm's selection; gripper dimensions are not counted as arm joints.

Recommendations and execution share `input_scope`: `channels`, `channels_by_arm`, `shared_channels`, `excluded_inputs`, and `missing_ownership`.
Unselected sides and source fields covered by component views are recorded as `not_checked`, not passed.
Unknown ownership makes coverage incomplete. Reports retain actual channels, components, and side-specific evidence per field without producing a cross-arm score.

## Video algorithms: optional joint evidence

```yaml
checks:
  Video_Integrity:
    input_mode: auto
    freeze_motion_review:
      rgb/head:
        - pose/arm/left/feedback
        - pose/arm/right/feedback
```

`freeze_motion_review` defaults to `{}`. Camera keys and motion channels accept logical names or exact source names.
This is an optional algorithm input binding; camera roles, feedback semantics, units, components, and clocks come from robot/data configuration.
The initial implementation supports only confirmed joint or end-effector `position` feedback, read through the existing SI interface.
Motion channels remain subject to the selected `arms`; missing optional channels do not block the original visual check.

Existing grayscale-hash freeze candidates are reused without changing repeated-frame detection. Each interval that can be associated includes:

- The original visual interval and up to 3 sample images (start, middle, end), without generating a preview video.
- An SVG plot of SI positions, source rows, times, units, and motion ranges. Large intervals use at most approximately 1001 plot points, explicitly recording the sampling stride and original sample count.
- The camera clock, time source, explicit clock mapping, and motion signal source/component selection.

Both `frames` and `video` inputs are supported. Explicitly selecting `video` retains its existing video materialization behavior; the joint evidence adds only PNG/SVG/JSON files.
Association requires comparable record-time sources and a shared clock, or an explicit configuration mapping. Selecting the head camera alone does not establish synchronization.
If time, units, or motion requirements are missing, `freeze_motion_review.status: unavailable` and its reason are retained; the original visual score and judgment stay unchanged.
No candidates produces `no_candidates`. When only some channels can be associated, each motion input records its own status.
A static view may agree with a stationary robot; robot motion can also occur outside the field of view. This evidence supports manual review and does not automatically establish copied frames or a fault.
