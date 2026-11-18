# Built-in value algorithms

[简体中文](VALUE_ALGORITHMS.zh-CN.md) · [Run algorithms](../how-to/RUN_AND_SELECT.md) · [Integration](../how-to/ALGORITHM_INTEGRATION.md)

Explicitly select these entries. They retain native distributions, events and experiment estimates, without cross-algorithm scores or automatic quality approval. The nine default quality methods and their order remain unchanged.

| ID | Scope | Computation |
|---|---|---|
| Initial_State_Distribution | Dataset | SAM3 initial frames, 2D distribution, optional depth geometry |
| Progress_Stagnation_Attribution | Episode | GRM progress, regression/stagnation, opt-in v8 suggestions |

## Installation and execution

Base installation contains implementations without model libraries, downloads, weights or GPU initialization. Install the same EDC wheel in model environments. Both methods support an isolated interpreter selected with python; source fingerprints must match. Initial distribution keeps in-process execution when python is omitted. See [model environments and dependencies](MODEL_ENVIRONMENTS.md) for installation, version combinations and checks.

| Method | Separate requirements |
|---|---|
| Initial distribution | SAM3, Torch, TorchVision, Pillow, local SAM3 |
| GRM | Torch, Transformers, vLLM, OpenCV, MoviePy, Matplotlib, local GRM; google-genai for automatic segmentation |

Missing resources produce diagnostics, not empty computations. Bundled SOURCE.json records provenance and unresolved licenses. Dependency licenses do not automatically cover modifications, weights or data.

Paths below are **example placeholders**. Compile robot cards first; full profiles work directly. See the [Python API](../how-to/RUN_AND_SELECT.md#python-api).

~~~python
from pathlib import Path
import yaml
import edc
profile = yaml.safe_load(Path('/path/to/profile.yaml').read_text())
algorithm_id = 'Initial_State_Distribution'
parameters = yaml.safe_load(Path('/path/to/algorithm-params.yaml').read_text())
with edc.open_dataset('/path/to/dataset', profile=profile) as dataset:
    recommendation = edc.recommend(dataset, params={algorithm_id: parameters})
    result = edc.run(dataset, output='/path/to/new-results',
        algorithm_ids=[algorithm_id], params={algorithm_id: parameters})
~~~

Follow each AlgorithmSpec.parameter_schema; recommendation and execution must share effective parameters.

## Initial-state distribution

~~~yaml
channel: head_rgb
checkpoint: /path/to/sam3.pt
comparison_space: fixed_camera_same_workspace
objects:
  - name: cup
    prompt: cup
    confidence_threshold: 0.5
frame_index: 0
grid_size: 8
device: cuda
~~~

comparison_space is user-confirmed, with no viewpoint correction. Category scores remain 100 × normalized_entropy × valid_episode_ratio; the total is their mean. Missing detections and failures retain original denominators. Frames, detections, heatmaps, statistics, selectors and errors remain available. Failures cannot imply complete assessment. The built-in ID has no silent alias for the retired plugin ID; historical results retain their identity.

### Optional spatial state

Explicit spatial parameters enable geometry independently of the **2D score**:

~~~yaml
spatial:
  source: sensor_depth  # or monocular_estimate, additionally requiring local model
  unit: m              # explicit m or mm
  registered_to_rgb: true
  registration_id: calibrated_camera_pair
  intrinsics: {fx: 500, fy: 500, cx: 320, cy: 240}
  episodes:
    stable_episode_id:
      path: /path/to/registered-depth.npy
      rgb_frame_index: 0
      depth_source_index: 0
      correspondence_evidence: verified_capture_mapping
~~~

Replace illustrative intrinsics with actual calibration. The 2D depth map must match RGB pixels exactly; no guessed units or resizing. Outputs retain camera coordinates, plane quality, heights and depth origin. Monocular model fingerprints do not establish sensor measurements. Depth/plane failure preserves 2D results and marks geometry incomplete. Geometry tests do not validate a specific camera or monocular scale. Sobol remains an independent local plugin, outside this score and framework packages.

## Progress and stagnation

EDC supplies original RGB images from three channels: `views` binds `cam_high`, `cam_left_wrist` and `cam_right_wrist`. `Episode.iter_frames()` reads stored images or decodes video. GRM consumes the same reference and before/after image sets for either storage type; only requested original images are cached as PNG. `frame_interval: 10` samples every ten reference frames, independently of playback rate.

`cam_high` is the reference sequence. Explicit shared-row mappings take precedence; otherwise EDC chooses the nearest actual image in a common clock or an explicitly mapped clock, preferring the earlier image on a tie. Different rates and counts, variable playback rates and repeated references to one image are supported. Requests outside a view's coverage remain missing; matching counts alone do not establish synchronization. `input-mapping.json` preserves episode ID, selector, channels, source indices, separate source/container/playback times, pairing evidence, offsets and coverage. Workflow quality-release references and data fingerprints are retained; an independent invocation does not claim upstream quality approval.

Native outputs retain the `subtask_<index>_<timestamp>/pred_vllm.json` layout and raw model responses, combined `pred_vllm.json`, `evaluation_summary.json`, `progress_chart.png` and `assessment_report.txt`. `attribution_result.json` and `attribution_report.txt` link each visual candidate to its subtask, original frames, source seconds, physical conclusion and missing evidence. Task progress curves are evaluated separately so a new task's reset is not used as evidence of an earlier task's regression.

`export_video` defaults to `false`. Set it to `true` to generate per-subtask and overall `reward_vis.mp4`, with left-wrist/head/right-wrist images and hop/progress plots. Playback shows sampled observations at 4 FPS; changing `frame_interval` changes the selected images and video length, not this display rate. Images correspond to `input_after_frame_index`. `visualization.json` records export status. Export failure preserves intermediate results and images.

`keep_frame_cache` defaults to `false`. After normal processing, including an incomplete physical assessment, EDC removes only this run's cached input PNGs; original data, plots and exported videos remain. `input-mapping.json` keeps source indices, selectors, RGB hashes and `edc-frame:` references so the images can be located again. Set it to `true` to retain cached images. Inference, attribution or export exceptions retain the cache for investigation. Without recorded source time, visual progress remains available, but no stagnation records do not mean stagnation was checked.

Required parameters are `model`, `task`, `physical_signals`, and exactly one of `subtasks` or `segmentation`. Each explicit subtask has `step`, `query`, and either:

- `clip_start_sec` / `clip_end_sec`: legacy clip seconds. Optional `subtask_time_basis: playback|source` selects the axis. Without it, native video playback seconds are used when available, otherwise recorded source seconds, relative to the first frame. The actual choice is saved.
- `clip_start_frame` / `clip_end_frame`: a half-open original reference interval `[start, end)`. Use this when images have order but no reliable seconds. Do not combine frame bounds with seconds bounds.

For example, these parameters select the first 100 reference images and infer visual progress even when physical bindings have not been configured:

~~~yaml
views: {cam_high: head_rgb, cam_left_wrist: left_rgb, cam_right_wrist: right_rgb}
model: /path/to/local-grm
python: /path/to/model-environment/bin/python
task: pick and place
physical_signals: {}
frame_interval: 10
subtasks:
  - {step: 0, query: pick and place, clip_start_frame: 0, clip_end_frame: 100}
filter_policy: none
~~~

Paths and channel names above are placeholders. For `edc run`, place algorithm parameters under `checks.Progress_Stagnation_Attribution` in a robot YAML, or use the workflow stage's `params` mapping. Example command:

~~~bash
edc run /path/to/data --robot /path/to/robot-with-progress.yaml --algorithm Progress_Stagnation_Attribution --output /path/to/new-results
~~~

Source time drives stagnation durations, velocity and physical event windows; playback timestamps do not prove acquisition timing. Ordered images without reliable time support visual progress, but not second-based stagnation or physical synchronization. `max_frame_gap_sec` is deprecated and ignored with a recorded warning; quality requirements remain in the quality stage. No constant-FPS, maximum frame-gap or source/playback-equality gate is applied here.

Physical bindings use left/right → role → configuration: channel, field, feedback semantics, optional unit conversion, max_gap_sec and indices. Roles are hand_position, hand_load, arm_load, ee_position, arm_velocity and joint_position. Robot declarations for auxiliary signals accept explicit quantity, source_kind, calibration_status: confirmed and calibration_reference; unknown units remain unknown. Available signals survive another role's failure. End-effector positions are converted to metres through EDC; interpolation limits are not camera frame-rate quality gates.

Hand position defaults to `grip_mode: references`, with confirmed `open_reference` / `closed_reference`. Alternatively, `grip_mode: native_relative`, `opening_direction: increasing` and `direction_reference` select the original scalar percentile/hysteresis rule without full-open/closed normalization. The declared input scale and original .01 floor are retained; observed extrema are not physical endpoint calibration. By default, both modes require confirmed robot semantics, supported units and calibration; algorithm parameters cannot upgrade unknown metadata. For an arm-load substitute, explicitly set `hand_load: {proxy_of: arm_load, reference: <applicability-evidence>}`. This preserves the proxy identity and does not relabel arm effort as measured finger current. Confirmed current signals in amperes use their RMS directly, without an additional adapter scale. Optional fixed rules are `load_rules: {unit: A, close_delta: 0.3, stagnation_empty_delta: 0.2}`: 0.3 A for before/after-close changes and 0.2 A for empty-grasp changes during stagnation; these are also the defaults. Gripper percentiles, load baseline, standard deviation and spike thresholds are computed by the algorithm and have no manual overrides. Load is a current proxy, not object weight. The acquisition protocol must establish the units of `joint_effort`; unknown values and torque are not treated as current. Explicit legacy `delta_threshold` scaling remains compatible, emits a migration notice and records its scale; it cannot be combined with new `load_rules`. Results retain effective rules, signal sources and before/after-close load evidence.

Explicit `evidence_policy: allow_provisional` permits conditional attribution from gripper/load signals whose robot metadata declares `calibration_status: pending`, units, quantity, feedback identity and a `calibration_reference`. The default `confirmed_only` still requires confirmation. Conditional outputs retain original calibration metadata, unit assumptions, load-proxy identity and per-candidate `provisional` status. They keep `assessment_complete: false`, cannot complete workflow valuation, and do not produce proactive grasp findings or `training_filter_v8` suggestions. Use explicit subtask intervals; automatic segmentation requirements are unchanged. Unknown units, mismatched rule units, commands or missing clocks remain unsupported. Fixed rules and automatic statistics are unchanged.

Declared joint torque in `N*m` is also supported with explicit `load_rules.unit: N*m`, `close_delta` and `stagnation_empty_delta`, plus signal `quantity: torque`. Applicable methods/operators must supply both thresholds; current defaults never transfer to torque. RMS retains torque values without a motor-constant conversion to current. Historical `cur_*` fields remain for compatibility; `load_rules` and `load_sources` specify their actual quantity and units. The torque branch runs regression/stagnation attribution only; it does not enable uncalibrated proactive scans or the current-based `training_filter_v8` rules.

`filter_policy` defaults to `none`. Explicit `training_filter_v8` produces suggestions without deletion, trimming or training. Proactive scanning requires verified native-rule mappings in hand position/load `v8_native_scale`: scale, offset, reference, verified: true. Time windows use source seconds; sample-count recovery rules and numerical thresholds retain their meanings. Failed attempts, later recovery, versions, intervals and raw responses are preserved. Recovery does not erase failure; a failed interval stops before the next gripper action.

Gemini segmentation requires method: gemini, model, api_key_env, allow_remote_upload: true, description, task subtasks. EDC creates dedicated 30 FPS presentation videos using source time, preserving observed duration to encoding resolution and saving `segmentation-video/video-mapping.json`. Feature plots and event logs use the same source-relative display axis. Returned intervals map back to original reference frames and source seconds. 30 FPS is an internal encoding parameter, not an input requirement. Both arms need calibrated openness, metre positions, ampere loads, radian joints and explicit segmentation scales. No upload occurs without opt-in; missing evidence cannot fabricate task boundaries.

Outputs separately record segmentation, visual evaluation, physical attribution and the selected filter branch. **Only complete attribution sets `assessment_complete: true`.** Missing time, signals, calibration or coverage keeps available visual results while leaving assessment incomplete; the workflow does not add those episodes to its completed value set. Runtime exceptions remain processing failures. Source/configuration/model fingerprints are saved before model initialization, and cached images and intermediate evidence remain discoverable through `catalog.json` on failure. `source_time_sec` and hop source times are relative to the first source frame; original frame bindings remain intact. Interface and rule tests do not establish task accuracy or training benefit.

## Evidence and failure

EDC creates edc_record_v1, catalog.json and artifacts. Dataset results normally use artifacts/dataset/; episode files are listed in record manifests. provenance.json precedes model initialization, with parameters, source/model fingerprints, versions and origin. Failures retain evidence. Provenance failure prevents inference. Processing errors, incomplete assessment and recommendations remain distinct.

## HTML result report

`edc run` writes a compact `report.html`: algorithm, object, native score, judgment, execution status, a short reason and detailed-result links. Pass/fail judgments come from the method; scores without pass thresholds have no assigned judgment, and insufficient evidence remains incomplete. Plots, heatmaps, videos and candidate details stay in the native output rather than expanding in HTML. Reports do not generate extra videos. `catalog.json` links each run's HTML; `--no-html` disables HTML while retaining JSON and evidence. Share the full output directory to preserve relative links.
