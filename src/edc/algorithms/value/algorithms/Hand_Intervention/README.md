# Hand intervention detection (Hand_Intervention)

[简体中文](README.zh-CN.md)

Checks whether a human hand/arm newly enters the task workspace, or touches or moves a task object, in any configured view. It is the hand part of the former four-event safety detection (Collision_Detection_Qwen3vl v62), without collision, spill, lid fall or geometry inputs, so no depth, intrinsics or arm calibration is needed.

1. **Scan**: 12-second windows (2-second overlap) of per-view timestamped contact sheets at `scan_fps` go to a local Qwen3-VL with the task text and previous hand state; it lists high-recall candidates.
2. **Merge**: candidates within 2 s are merged, up to 14 s.
3. **Review**: each merged interval ±1 s is re-checked at `review_fps` as confirmed, needs_review or rejected. Confirmation needs a visible contact or object movement in the evidence chain.
4. **Verdict**: any confirmed event gives FAIL; needs-review events or failed windows give NEEDS_REVIEW; otherwise PASS.

Views missing from an episode are skipped and reported in `missing_views`.

```bash
edc run /path/to/episode.mcap --robot configs/robots/x1pro_mcap.yaml --algorithm Hand_Intervention --output /path/to/results
```

Parameters under `checks.Hand_Intervention`: `views` (`{label: camera}`), `model` (Qwen3-VL, shared with Initial_Placement_Assessment), optional `task` / `task_topic`, `window_seconds`, `scan_fps`, `review_fps`. Non-rejected events keep one review contact sheet per view.

**Validation (Jiangxi X1 Pro, 269 episodes, Qwen3-VL-4B, 2026-11-26):** 9 of 10 human-labelled S03 episodes flagged (8 FAIL, 1 NEEDS_REVIEW); 3 of 259 unlabelled episodes flagged, one showing a real hand moving the spoon (likely a missed label) and one a background person (false alarm). The labelled sample is small.
