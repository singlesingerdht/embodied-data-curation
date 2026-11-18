# Built-in value algorithms

[简体中文](VALUE_ALGORITHMS.zh-CN.md) · [Run algorithms](../how-to/RUN_AND_SELECT.md) · [Integration](../how-to/ALGORITHM_INTEGRATION.md)

Explicitly select these entries. They retain native distributions, events and experiment estimates, without cross-algorithm scores or automatic quality approval. The nine default quality methods and their order remain unchanged.

| ID | Scope | Computation |
|---|---|---|
| Initial_State_Distribution | Dataset | SAM3 initial frames, 2D distribution, optional depth geometry |
| Progress_Stagnation_Attribution | Episode | GRM progress, regression/stagnation, opt-in v8 suggestions |
| Collision_Detection_Qwen3vl | Episode | Four safety events, candidate adjudication and image evidence |

## Installation and execution

Base installation contains implementations without model libraries, downloads, weights or GPU initialization. Install the same EDC wheel in model environments. All three methods support an isolated interpreter selected with python; source fingerprints must match. Initial distribution keeps in-process execution when python is omitted. See [model environments and dependencies](MODEL_ENVIRONMENTS.md) for installation, version combinations and checks.

| Method | Separate requirements |
|---|---|
| Initial distribution | SAM3, Torch, TorchVision, Pillow, local SAM3 |
| GRM | Torch, Transformers, vLLM, OpenCV, MoviePy, Matplotlib, local GRM; google-genai for automatic segmentation |
| Safety | Torch, Transformers, SAM3, CoTracker3, DINOv2, Qwen3-VL; optional DA3 for RGB-only depth preparation |

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

### Install the worker and download SAM3

Run these commands from the framework source directory, replacing every `/path/to/...` with an absolute local path. The main environment needs Python 3.10 or later; the worker needs Linux x86_64, Python 3.12 and an available NVIDIA GPU. Build one EDC wheel for both environments:

```bash
python -m pip install build
python -m build --wheel --outdir /path/to/edc-dist
python -m pip install '/path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl[formats,initial-state]'
```

Build a wheel from a pinned revision of the [official SAM3 source](https://github.com/facebookresearch/sam3). Keep this source and its weights outside the EDC repository:

```bash
git clone https://github.com/facebookresearch/sam3.git /path/to/sam3-source
git -C /path/to/sam3-source checkout 8f0b7f4d4e7eda2ed606ebde6702c93359ad01da
python -m pip wheel --no-deps /path/to/sam3-source --wheel-dir /path/to/source-wheels

edc env create vision --with initial-state \
  --python /path/to/python3.12 \
  --venv /path/to/envs/initial-state \
  --edc-wheel /path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source sam3=/path/to/source-wheels/sam3-0.1.0-py3-none-any.whl
edc env check --venv /path/to/envs/initial-state --imports
```

The recipe pins Torch 2.8.0, TorchVision 0.23.0, Transformers 4.57.1 and NumPy 1.26.4. A new worker can include both `--with progress --with initial-state`; point both algorithms' `python` settings at that worker. The base installation contains no model libraries or weights.

Request access and accept the applicable terms on the [facebook/sam3 model page](https://huggingface.co/facebook/sam3), then log in and download the checkpoint. This implementation uses native `sam3.pt`, not the Transformers `model.safetensors` file:

```bash
/path/to/envs/initial-state/bin/hf auth login
/path/to/envs/initial-state/bin/python - <<'PYTHON'
from huggingface_hub import hf_hub_download
hf_hub_download(
    repo_id="facebook/sam3",
    filename="sam3.pt",
    revision="3c879f39826c281e95690f02c7821c4de09afae7",
    local_dir="/path/to/models/sam3",
)
PYTHON
```

For Hugging Face Mirror, first set `export HF_ENDPOINT=https://hf-mirror.com` in the download terminal; access requirements still apply. Existing complete local weights can be reused. The selected `sam3.pt` has SHA-256 `9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e`; check it with `sha256sum /path/to/models/sam3/sam3.pt`.

Use CUDA for SAM3; some upstream CPU paths still create CUDA tensors. Optional spatial geometry requires actual calibration. Depth is unnecessary for a 2D run.

### Inputs and parameters

EDC reads the selected initial RGB frame from each episode through `Episode.iter_frames()`. Images and decodable videos are supported sources. The 2D method requires no fixed FPS, three-view synchronization or physical signals. Confirm that the selected episodes share a comparable camera and scene.

Merge this `checks` section into a robot YAML suited to the dataset, retaining its data and camera mappings. All `/path/to/...` values are placeholders; replace `faceImg` with the actual channel name.

```yaml
checks:
  Initial_State_Distribution:
    channel: faceImg
    checkpoint: /path/to/sam3.pt
    comparison_space: fixed_camera_same_workspace
    objects:
      - name: cup
        prompt: cup
        confidence_threshold: 0.5
    python: /path/to/sam3-env/bin/python
    device: cuda
    frame_index: 0
    grid_size: 8
```

The model environment needs SAM3, Torch, TorchVision, Pillow and the exact same EDC method source as the calling environment. Omit `python` to use the current interpreter.

```bash
edc algorithms
edc run /path/to/dataset \
  --robot /path/to/robot-initial-state.yaml \
  --algorithm Initial_State_Distribution \
  --output /path/to/results/initial-state
```

By default, the method aggregates all selected dataset episodes. `--episode 0` selects only the first episode for a single-episode check. This can verify detection and outputs, but cannot establish coverage of a whole dataset. Multiple object instances within one frame also contribute to the distribution.

### Complete example: initial cup positions across episodes

Copy the [initial-state configuration](../../configs/robots/initial_state_example.yaml) from the source repository and set `checkpoint`, `python` and the camera channel. Select episodes with comparable viewpoints and workspaces, explicitly listing them in `dataset.yaml` at the dataset root:

```yaml
schema_version: 1
id: initial_cup_example
episodes:
  - {id: episode_a, path: episode_a, format: episode}
  - {id: episode_b, path: episode_b, format: episode}
```

This illustrates the layout; replace `episode_a`/`episode_b` with stable episode IDs and relative paths. Each episode directory contains its original JSON recording and `faceImg.mp4`; video frame 0 is used. Users supply their data; the repository provides configuration and instructions. Retain existing supported collection metadata rather than redefining episode boundaries.

```bash
cp configs/robots/initial_state_example.yaml /path/to/robot-initial-state.yaml
# Edit model/interpreter paths and the scene group in that copy, then run:
edc robot check --robot /path/to/robot-initial-state.yaml --data /path/to/dataset
edc run /path/to/dataset \
  --robot /path/to/robot-initial-state.yaml \
  --algorithm Initial_State_Distribution \
  --output /path/to/results/initial-state
```

Open `report.html` for the native score and follow `catalog.json` to detections, heatmaps and statistics. Check source episode counts, extraction errors, per-category valid ratios and boxes. Record `status: completed` together with native `assessment_complete: true` establishes complete evaluation of the selected 2D scope. A valid ratio of zero can mean no detections; it does not establish adequate coverage.

Scores are `100 × normalized_entropy × valid_episode_ratio`, with **no default pass threshold**. Small samples describe only their selected distribution, not overall coverage adequacy; viewpoint shifts also change image-grid positions. Instances count separately: `cup` does not identify individual cups across episodes. Inspect detections first: a broad `teapot` prompt may include both a ceramic teapot and a metal kettle. Clarify the target prompt and rerun before using mixed-category scores for collection decisions.

### Outputs and failures

Find the run through `catalog.json` in the output directory. `_runs/<run_id>/Initial_State_Distribution/records.jsonl` contains the `edc_record_v1` record; native evidence is under that algorithm's `artifacts/dataset/` directory:

| File | Contents |
|---|---|
| `initial_state_detections.json` | Per-episode boxes, confidence, centroids and frame sources |
| `distribution_summary.json` | Grid distribution, entropy, valid-episode ratio and native scores |
| `*-initial.png`, `*-probability.png` | Initial input images and category probability heatmaps |
| `input-mapping.json` | Episode, channel and frame mappings in isolated worker mode |
| `provenance.json`, `config.json` | Model/method provenance and effective configuration |
| `domain-errors.json`, `spatial-errors.json` | Extraction and spatial errors after computation begins |

Check each record's `status` and reason. Missing models/dependencies, unavailable channels and out-of-range frame requests are preflight diagnostics. Decode or detection failures retain existing evidence and record failure. No detections is distinct from unreadable input; the original episode count remains in the denominator. A preflight failure may have no model provenance file. Once execution begins, provenance is saved before model initialization.


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

## From model preparation to complete attribution

A complete run needs three camera views, subtask intervals, aligned recorded time, and both arms' gripper opening, load and end-effector motion feedback. This walkthrough uses existing subtask annotations for GRM and physical attribution, without remote segmentation or v8 filtering. Downloading a model does not supply the required input evidence.

### 1. Install matching framework and worker versions

Start from EDC source containing this algorithm. All paths below are examples; use new output directories. The main environment needs Python 3.10 or later. The model worker needs Linux x86_64, Python 3.12 and an NVIDIA GPU usable by Torch/vLLM. Memory use depends on image sizes and execution parameters; no minimum VRAM is promised here.

```bash
python -m pip install build
python -m build --wheel --outdir /path/to/edc-dist
python -m pip install '/path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl[formats,value-inputs]'

edc env create vision --with progress \
  --python /path/to/python3.12 \
  --venv /path/to/envs/progress \
  --edc-wheel /path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl
edc env check --venv /path/to/envs/progress --imports
edc algorithms
```

The list must include `Progress_Stagnation_Attribution`. The worker pins Torch 2.8.0, Transformers 4.57.1 and vLLM 0.11.0. Do not substitute generic Qwen weights for GRM or upgrade Transformers independently. The environment check does not run inference.

### 2. Download the GRM checkpoint

Use [Robo-Dopamine-GRM-2.0-8B-Preview](https://huggingface.co/tanhuajie2001/Robo-Dopamine-GRM-2.0-8B-Preview). The command pins a revision and downloads the complete directory, including weight shards, configuration, tokenizer and image processor resources. See the [Hugging Face download guide](https://huggingface.co/docs/huggingface_hub/guides/download).

```bash
/path/to/envs/progress/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="tanhuajie2001/Robo-Dopamine-GRM-2.0-8B-Preview",
    revision="6521e2661c265b4c5561fd21fa326a975b323718",
    local_dir="/path/to/models/grm",
)
PY
```

To use Hugging Face Mirror, first set `export HF_ENDPOINT=https://hf-mirror.com` in the same terminal, keeping the model ID and revision unchanged. This example uses a standalone model directory; an existing complete checkpoint can also be used. Check model provenance and licensing against its model card. Framework packages do not contain weights.

### 3. Prepare data and the complete robot configuration

Copy the [complete dual-arm configuration example](../../configs/robots/progress_example.yaml), adapt it to your recording and save it as `/path/to/robot-progress.yaml`. It describes an illustrative input contract, not a real robot calibration; its example confirmation statements are not evidence for your hardware. When adapting an existing robot configuration, retain its channel declarations and add the relevant algorithm settings.

The example expects one episode directory containing `trajectory.json`, `head_rgb.mp4`, `left_rgb.mp4` and `right_rgb.mp4`. JSON has the shape `{"data": [...]}` with these fields in each row:

| Field | Meaning and shape |
|---|---|
| `timestamp` | Recorded acquisition seconds, paired through the declared video-frame mapping |
| `left_gripper` / `right_gripper` | Scalar opening distance in cm, increasing as the gripper opens |
| `left_effort` / `right_effort` | Confirmed joint-current arrays in A; the example explicitly uses whole-array RMS as a load proxy |
| `left_xyz` / `right_xyz` | End-effector feedback `[x, y, z]` in m, each in its own fixed coordinate frame |

Use `video_frame_time: rows` only when the recording protocol explicitly pairs every video frame with a row. Equal counts do not establish that relationship. For other formats or sampling rates, retain the relevant reader/robot source-time and clock mappings. Original image sequences are also supported without video encoding.

The configuration provides both arms' complete `physical_signals`: gripper position, arm load, an explicit hand-load proxy and end-effector position. Replace fields, units, opening direction and confirmation references with actual recording information. A field named `joint_effort` does not establish current units. For torque, declare `unit: N*m` and `quantity: torque`, and explicitly supply both applicable torque thresholds; ampere thresholds do not transfer automatically.

The two example intervals `[0, 100)` and `[100, 200)` illustrate the format only. Replace them and the task descriptions with the episode's actual annotations. Views and physical evidence must cover the evaluation range. The current implementation checks both arms; fabricated all-zero signals cannot substitute for an unrecorded side.

**The repository currently provides no publicly downloadable real demonstration with complete physical signals and calibration.** This is therefore a complete configuration example for your own matching data, not a data bundle that reproduces a real demonstration after downloading the model. An upstream GRM video-only demo can demonstrate visual progress but cannot supply physical attribution evidence.

### 4. Check the input and run

```bash
edc robot check --robot /path/to/robot-progress.yaml --data /path/to/episode
edc run /path/to/episode \
  --robot /path/to/robot-progress.yaml \
  --algorithm Progress_Stagnation_Attribution \
  --output /path/to/new-results
```

`robot check` checks that declared channels can be read; it does not prove attribution completeness. `edc run` uses the configured worker interpreter automatically, without manually activating that environment.

### 5. Verify the complete result

Open `report.html` at the output root and use `catalog.json` to locate records and evidence. A complete run requires record `status: completed`, native `assessment_complete: true`, and `components.segmentation`, `visual` and `physical` all marked `completed`. For this example, `filter` is `not_requested` because v8 was not selected.

Native outputs include per-subtask progress and model responses, combined `progress_chart.png`, `evaluation_summary.json`, `attribution_result.json` and readable `attribution_report.txt`. Attribution links original frames/times, candidate issues, physical conclusions and evidence. A complete evaluation can find no events. Detecting an issue is not an execution failure, and a particular label is not guaranteed in advance.

Missing inputs or calibration preserve partial results and mark assessment incomplete. JSON/HTML output or a successful command exit alone does not establish completeness. Video is disabled by default; use `export_video: true` to request review videos. Normal completion removes the run's input-image cache by default while retaining source mappings and result evidence.


### X1 Pro pour-tea example with non-G grippers

The [complete X1 Pro configuration](../../configs/robots/x1pro_progress_example.yaml) uses `faceImg`, `leftImg`, `rightImg` and JSON fields `timestamp`, `follow_left/right_gripper`, `follow_left/right_joint_effort`, `follow_left/right_position`. Both grippers record centimetres, increasing when opening. Effort is declared as N·m under this example's capture contract, using all seven values' RMS as a proxy with fixed change thresholds of 0.3/0.2 N·m. These declarations do not apply to every X1 Pro gripper or export format.

Intervals come from human annotations for `pourtea_lzy_0720@MASTER_SLAVE_MODE@2026_07_20_10_55_07`: original step 1 is `[332, 908)` for grasping the kettle; original step 5 is `[2808, 4337)` for grasping the teapot and pouring into the first cup. Only these two intervals are assessed, not the entire task. Native `subtask_0`/`subtask_1` names follow the selected order; original step IDs and intervals remain in `components.segmentation.intervals`. Keep the original JSON and three named MP4s in the episode directory. `video_frame_time: rows` declares the recording protocol's explicit row/frame correspondence. For another episode, replace annotations and signal declarations with applicable information.

```bash
cp configs/robots/x1pro_progress_example.yaml /path/to/robot-progress.yaml
# Edit local model and python paths; other recordings also need their own metadata/subtasks.
edc robot check --robot /path/to/robot-progress.yaml --data /path/to/pourtea-episode
edc run /path/to/pourtea-episode \
  --robot /path/to/robot-progress.yaml \
  --algorithm Progress_Stagnation_Attribution \
  --output /path/to/results/progress
```

The repository supplies configuration and instructions, not this real recording; data holders supply it locally. Check the completion conditions above, then review images at the source frames/times in `attribution_report.txt`. A GRM regression candidate is not a confirmed empty grasp; seven-value RMS can also reflect arm motion. `assessment_complete` establishes execution and sufficient input evidence for the selected branches, not the accuracy of every attribution label. Review false positives and missed events against the images rather than adjusting thresholds to force a desired label.


## Progress and stagnation

EDC supplies original RGB images from three channels: `views` binds `cam_high`, `cam_left_wrist` and `cam_right_wrist`. `Episode.iter_frames()` reads stored images or decodes video. GRM consumes the same reference and before/after image sets for either storage type; only requested original images are cached as PNG. `frame_interval: 10` samples every ten reference frames, independently of playback rate.

`cam_high` is the reference sequence. Explicit shared-row mappings take precedence; otherwise EDC chooses the nearest actual image in a common clock or an explicitly mapped clock, preferring the earlier image on a tie. Different rates and counts, variable playback rates and repeated references to one image are supported. Requests outside a view's coverage remain missing; matching counts alone do not establish synchronization. `input-mapping.json` preserves episode ID, selector, channels, source indices, separate source/container/playback times, pairing evidence, offsets and coverage. Workflow quality-release references and data fingerprints are retained; an independent invocation does not claim upstream quality approval.

Native outputs retain the `subtask_<index>_<timestamp>/pred_vllm.json` layout and raw model responses, combined `pred_vllm.json`, `evaluation_summary.json`, `progress_chart.png` and `assessment_report.txt`. `attribution_result.json` and `attribution_report.txt` link each visual candidate to its subtask, original frames, source seconds, physical conclusion and missing evidence. Task progress curves are evaluated separately so a new task's reset is not used as evidence of an earlier task's regression.

`export_video` defaults to `false`. Set it to `true` to generate per-subtask and overall `reward_vis.mp4`, with left-wrist/head/right-wrist images and hop/progress plots. Playback shows sampled observations at 4 FPS; changing `frame_interval` changes the selected images and video length, not this display rate. Images correspond to `input_after_frame_index`. `visualization.json` records export status. Export failure preserves intermediate results and images.

`keep_frame_cache` defaults to `false`. After normal processing, including an incomplete physical assessment, EDC removes only this run's cached input PNGs; original data, plots and exported videos remain. `input-mapping.json` keeps source indices, selectors, RGB hashes and `edc-frame:` references so the images can be located again. Set it to `true` to retain cached images. Inference, attribution or export exceptions retain the cache for investigation. Without recorded source time, visual progress remains available, but no stagnation records do not mean stagnation was checked.

Required parameters are `model`, `task`, `physical_signals`, and exactly one of `subtasks` or `segmentation`. Each explicit subtask has `step`, `query`, and either:

- `clip_start_sec` / `clip_end_sec`: legacy clip seconds. Optional `subtask_time_basis: playback|source` selects the axis. Without it, native video playback seconds are used when available, otherwise recorded source seconds, relative to the first frame. The actual choice is saved.
- `clip_start_frame` / `clip_end_frame`: a half-open original reference interval `[start, end)`. Use this when images have order but no reliable seconds. Do not combine frame bounds with seconds bounds.

The following is a visual-only smoke example selecting the first 100 reference images. `physical_signals: {}` cannot complete physical attribution; use the complete configuration above for that:

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

## Four-event safety detection

Run the native v62 collision, spill, hand-intervention and lid-fall checks with `edc run`. Results preserve candidates, adjudications, completion and evidence. Visible hands alone do not establish intervention: the rules require resolved contact or human-driven object change. This method has no general numerical score and never deletes raw data.

### 1. Install the framework and worker

Run the following commands from the EDC repository root. The main interpreter needs Python 3.10+. The worker needs Linux x86_64, Python 3.12 and an available NVIDIA GPU. Replace every `/path/to/...` placeholder. Keep dependency sources, models and results outside the repository. Install the same EDC wheel in the caller and worker.

```bash
python -m pip install build
python -m build --wheel --outdir /path/to/edc-dist
python -m pip install '/path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl[formats,value-inputs]'

git clone https://github.com/facebookresearch/sam3.git /path/to/sam3-source
git -C /path/to/sam3-source checkout 8f0b7f4d4e7eda2ed606ebde6702c93359ad01da
git clone https://github.com/facebookresearch/co-tracker.git /path/to/cotracker-source
git -C /path/to/cotracker-source checkout 82e02e8029753ad4ef13cf06be7f4fc5facdda4d
/path/to/python3.12 -m pip wheel --no-deps /path/to/sam3-source --wheel-dir /path/to/source-wheels
/path/to/python3.12 -m pip wheel --no-deps /path/to/cotracker-source --wheel-dir /path/to/source-wheels

edc env create vision --with safety \
  --python /path/to/python3.12 --venv /path/to/envs/safety \
  --edc-wheel /path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source sam3=/path/to/source-wheels/sam3-0.1.0-py3-none-any.whl \
  --source cotracker=/path/to/source-wheels/cotracker-3.0-py3-none-any.whl
edc env check --venv /path/to/envs/safety --imports
edc algorithms
```

The recipe pins Torch 2.8.0, TorchVision 0.23.0, Transformers 4.57.1 and NumPy 1.26.4. Build SAM3 and CoTracker wheels from the pinned sources, using the actual filenames produced. `--with safety` uses local Transformers inference and requires no vLLM. Grounding and adjudication share one Qwen model. Memory and time depend on video length, resolution and candidate count. The base installation contains no GPU dependencies or weights.

### 2. Download the four models

Use [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct), [SAM3](https://huggingface.co/facebook/sam3), [CoTracker3](https://huggingface.co/facebook/cotracker3) and [DINOv2-small](https://huggingface.co/facebook/dinov2-small). Request SAM3 access and accept its terms before authenticating. Mirrors do not remove access requirements. Use native `sam3.pt` and CoTracker3 offline `scaled_offline.pth`; do not substitute GRM weights or another Qwen size.

```bash
export HF_HOME=/path/to/huggingface
export EDC_MODELS=/path/to/models
## Optional mirror:
export HF_ENDPOINT=https://hf-mirror.com
/path/to/envs/safety/bin/hf auth login
/path/to/envs/safety/bin/python - <<'PYTHON'
import os
from pathlib import Path
from huggingface_hub import snapshot_download, hf_hub_download
root = Path(os.environ['EDC_MODELS'])
for repo, revision, folder in [
    ('Qwen/Qwen3-VL-8B-Instruct', '0c351dd01ed87e9c1b53cbc748cba10e6187ff3b', 'qwen3-vl'),
    ('facebook/dinov2-small', 'ed25f3a31f01632728cabb09d1542f84ab7b0056', 'dinov2-small'),
]:
    snapshot_download(repo, revision=revision, local_dir=root/folder)
for repo, revision, filename, folder in [
    ('facebook/sam3', '3c879f39826c281e95690f02c7821c4de09afae7', 'sam3.pt', 'sam3'),
    ('facebook/cotracker3', 'bf55ea50d4390e1820a267f131cd6587240fb2c5', 'scaled_offline.pth', 'cotracker3'),
]:
    hf_hub_download(repo, filename, revision=revision, local_dir=root/folder)
PYTHON
```

Existing complete local models can be reused. SAM3 SHA-256: `9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e`. CoTracker SHA-256: `2670d4562ed69326dda775a26e54883925cd11b6fc9b24cb7aa9f8078bce7834`. EDC records the fingerprints of the actual files used at runtime.

### 3. Prepare data and configuration

The [pour-tea configuration](../../configs/robots/x1pro_safety_example.yaml) and `src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/pour_tea_task_graph.json` describe `pourtea_lzy_0720@MASTER_SLAVE_MODE@2026_07_20_10_55_07`, evaluating only original frames **[332, 692)**. The data holder supplies the real recording; the repository does not include a public data download. For another recording, update the stable episode ID, range, task graph and applicable calibration.

The episode directory contains `faceImg.mp4`, `leftImg.mp4`, `rightImg.mp4` and its original JSON. Each JSON `data` row provides `timestamp`, `follow_left/right_position`, `follow_left/right_rotation`, `follow_left/right_gripper` and `follow_left/right_joint_effort`. The example uses the capture protocol's explicit row-to-frame correspondence; matching frame counts do not prove synchronization.

| Input | Requirement |
|---|---|
| RGB | Original EDC images or decoded video frames; reference `faceImg`, other views paired by explicit mapping or a common clock; no fixed FPS requirement |
| Time | Recorded acquisition/container time; playback keeps its identity; missing coverage is never padded with the last frame |
| End effector | Position in metres and explicit orientation; `quaternion.order` accepts `xyzw`, `wxyz` or `rotvec` in rad, converted to native xyzw |
| Gripper/load | Framework-confirmed feedback and units; this example uses cm and N·m, with the seventh effort component as the frozen method's terminal-component proxy, not proven gripper torque |
| Geometry | 3×3 intrinsics, each arm's 4×4 arm-to-camera transform, depth `[frames,height,width]` in m/mm corresponding to selected RGB |
| Task graph | Entities, contact policies and four capabilities; disabling a check cannot establish completion of all four |

This example reuses legacy `pour_tea_io.py` intrinsics, transforms and rotation-vector interpretation. The transforms retain the original approximation and are not independently recalibrated. Gripper geometry uses metres; v62 closure rules retain their original centimetre scale and thresholds. Algorithm bindings cannot promote unknown units, feedback identity or calibration to confirmed status.

```bash
cp configs/robots/x1pro_safety_example.yaml /path/to/robot-safety.yaml
cp src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/pour_tea_task_graph.json /path/to/pour_tea_task_graph.json
```

Edit data and model paths before preparing depth. Existing sensor depth may use `source: sensor_depth` with units and registration evidence. Monocular estimates must not be labelled sensor measurements.

### 4. Optional depth preparation for RGB recordings

The RGB-only example uses [DA3NESTED-GIANT-LARGE-1.1](https://huggingface.co/depth-anything/DA3NESTED-GIANT-LARGE-1.1). Add `safety-depth` in a new environment; existing workers are not modified:

```bash
git clone https://github.com/ByteDance-Seed/Depth-Anything-3.git /path/to/da3-source
git -C /path/to/da3-source checkout 3d835ec1a5802d64a8b8b15f817a1ab54809bfe4
/path/to/python3.12 -m pip wheel --no-deps /path/to/da3-source --wheel-dir /path/to/source-wheels
edc env create vision --with safety --with safety-depth \
  --python /path/to/python3.12 --venv /path/to/envs/safety-depth \
  --edc-wheel /path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source sam3=/path/to/source-wheels/sam3-0.1.0-py3-none-any.whl \
  --source cotracker=/path/to/source-wheels/cotracker-3.0-py3-none-any.whl \
  --source depth-anything-3=/path/to/source-wheels/depth_anything_3-0.0.0-py3-none-any.whl
edc env check --venv /path/to/envs/safety-depth --imports
/path/to/envs/safety-depth/bin/python - <<'PYTHON'
import os
from pathlib import Path
from huggingface_hub import snapshot_download
snapshot_download('depth-anything/DA3NESTED-GIANT-LARGE-1.1',
    revision='b2359bdf726fb44ef62acca04d629dcf158053e7',
    allow_patterns=['config.json', 'model.safetensors', 'README.md'],
    local_dir=Path(os.environ['EDC_MODELS'])/'da3')
PYTHON
```

If the mirror returns `FileMetadataError` for file metadata, download the same pinned revision directly and verify its checksum. Do not prepare depth after a checksum failure:

```bash
mkdir -p "$EDC_MODELS/da3"
curl -fL --retry 3 https://hf-mirror.com/depth-anything/DA3NESTED-GIANT-LARGE-1.1/resolve/b2359bdf726fb44ef62acca04d629dcf158053e7/config.json -o "$EDC_MODELS/da3/config.json"
curl -fL --retry 3 https://hf-mirror.com/depth-anything/DA3NESTED-GIANT-LARGE-1.1/resolve/b2359bdf726fb44ef62acca04d629dcf158053e7/model.safetensors -o "$EDC_MODELS/da3/model.safetensors"
printf '%s  %s\n' 8ebe871a022ed58d2fc8fdfb2ebdb31d57b60fe39611c849095851a7b7c6020c "$EDC_MODELS/da3/model.safetensors" | sha256sum -c -
```

```bash
/path/to/envs/safety-depth/bin/python -m edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.prepare_depth \
  /path/to/pourtea-episode --robot /path/to/robot-safety.yaml \
  --model /path/to/models/da3 --output /path/to/depth \
  --frame-range 332 692 --depth-scale 0.9414349364434543 --device cuda:0
```

Preparation reads selected original RGB through EDC, estimates every frame, and saves RGB checksums, original indices, estimated intrinsics and correspondence. Source data stays unchanged. `depth_scale` defaults to 1; this example explicitly retains the legacy `718.409 / 763.1` factor for reproduction, not general calibration. Monocular depth can be inaccurate under occlusion, reflections and transparency.

Set `depth.episodes.<episode ID>.path` to `depth.npy`, `correspondence_evidence` to its `provenance.json`, and the depth `frame_range` to the selected range. The array must match selected original RGB frame count and resolution. Missing depth is not silently filled. For a whole-episode run, remove the algorithm `frame_range`, prepare whole-episode depth and update the depth range accordingly.

### 5. Run and inspect results

```bash
## Edit all /path/to/... values in robot-safety.yaml before running.
edc robot check --robot /path/to/robot-safety.yaml --data /path/to/pourtea-episode
edc run /path/to/pourtea-episode \
  --robot /path/to/robot-safety.yaml \
  --algorithm Collision_Detection_Qwen3vl \
  --output /path/to/new-safety-results
```

The `python` parameter selects the independent worker without manual activation. Set it to the `safety-depth` environment if that is the worker you created. Use a new output directory outside raw data.

Open root `report.html` for a compact pass/fail/review judgment, reason and full-result link. Pass means checks completed for the selected scope without confirmed or unresolved events; it is not safety certification. No numerical score is invented. `catalog.json` indexes records and evidence.

A complete execution requires `records.jsonl` status `completed`, `native-result.json` field `assessment_complete: true`, and all four `checks` statuses `completed`. Confirmed events produce fail; unresolved events produce review. Neither is a processing failure. Check `assessment_scope.original_frame_range`: completion of the example does not establish assessment of the whole recording.

| Evidence | Contents |
|---|---|
| `input-mapping.json`, `physical-mapping.json` | Original frames/times, view pairing, signal units and conversions |
| `input/video-mapping.json` | Internal 30 FPS presentation video to original-frame mapping; 30 FPS is an encoding parameter |
| `provenance.json`, `worker.log` | Model/source/configuration fingerprints and diagnostics |
| `native/safety_report.json` | Four-event native summary and scan coverage |
| `native/events_adjudicated.json` | Candidates, confirmed/rejected/review judgments and supporting reasons |
| `native/model-requests/`, `native/collision/` | Request images, raw responses, masks, tracks and geometry |
| `native-result.json` | Completion, compact judgment and actual scope |

Artifacts live under `_runs/<run_id>/Collision_Detection_Qwen3vl/artifacts/<episode-key>/`. Full review videos are not exported by default. Internal scan videos, cached frames and geometry arrays support execution and traceability; they are not original recordings. Missing inputs/dependencies, inference failures and incomplete checks retain existing evidence. Successful command exit, an HTML file or absent event files alone do not prove completion.

### Sources and scope

Implementation: `src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/`, registered through `AlgorithmSpec + run(context, params)`, returning `edc_record_v1` without research-repository or DQS imports. Candidate extraction and adjudication preserve the frozen v62 method. Detection accuracy needs separate task-specific evaluation.

`SOURCE.json` records original hashes and unresolved source licensing. Dependencies and models retain their respective terms: CoTracker and this DA3 model have non-commercial restrictions; SAM3 has separate terms. EDC packages exclude model weights, raw data, historical outputs and internal audits.
