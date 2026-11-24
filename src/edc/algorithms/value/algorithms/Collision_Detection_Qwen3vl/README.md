# Collision_Detection_Qwen3vl

[简体中文](README.zh-CN.md)

Run the native v62 collision, spill, hand-intervention and lid-fall checks with `edc run`. Results preserve candidates, adjudications, completion and evidence. Visible hands alone do not establish intervention: the rules require resolved contact or human-driven object change. This method has no general numerical score and never deletes raw data.

## 1. Install the framework and worker

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

## 2. Download the four models

Use [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct), [SAM3](https://huggingface.co/facebook/sam3), [CoTracker3](https://huggingface.co/facebook/cotracker3) and [DINOv2-small](https://huggingface.co/facebook/dinov2-small). Request SAM3 access and accept its terms before authenticating. Mirrors do not remove access requirements. Use native `sam3.pt` and CoTracker3 offline `scaled_offline.pth`; do not substitute GRM weights or another Qwen size.

```bash
export HF_HOME=/path/to/huggingface
export EDC_MODELS=/path/to/models
# Optional mirror:
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

## 3. Prepare data and configuration

The [pour-tea configuration](../../../../../../configs/robots/x1pro_safety_example.yaml) and this directory's `pour_tea_task_graph.json` describe `pourtea_lzy_0720@MASTER_SLAVE_MODE@2026_07_20_10_55_07`, evaluating only original frames **[332, 692)**. The data holder supplies the real recording; the repository does not include a public data download. For another recording, update the stable episode ID, range, task graph and applicable calibration.

The episode directory contains `faceImg.mp4`, `leftImg.mp4`, `rightImg.mp4` and its original JSON. Each JSON `data` row provides `timestamp`, `follow_left/right_position`, `follow_left/right_rotation`, `follow_left/right_gripper` and `follow_left/right_joint_effort`. The example uses the capture protocol's explicit row-to-frame correspondence; matching frame counts do not prove synchronization.

| Input | Requirement |
|---|---|
| RGB | Original EDC images or decoded video frames; reference `faceImg`, other views paired by explicit mapping or a common clock; no fixed FPS requirement |
| Time | Recorded acquisition/container time; playback keeps its identity; missing coverage is never padded with the last frame |
| End effector | Position in metres; canonical EDC `orientation` is consumed as XYZW. For raw fields, `quaternion.order` accepts `xyzw`, `wxyz` or `rotvec` in rad |
| Gripper/load | Framework-confirmed feedback and units; this example uses cm and N·m, with the seventh effort component as the frozen method's terminal-component proxy, not proven gripper torque |
| Geometry | Zero-skew pinhole 3×3 intrinsics, each arm's 4×4 arm-to-camera transform, depth `[frames,height,width]` in m/mm corresponding to selected RGB |
| Task graph | Entities, contact policies and four capabilities; disabling a check cannot establish completion of all four |

For quaternion feedback, prefer the EDC `orientation` field with an explicit representation. A raw quaternion field requires dimensionless unit `1` in a full profile; the compact robot YAML example uses `rotvec` with unit `rad`.

View and signal bindings accept either logical channel names or original names. Physical alignment covers the selected original frames, retaining source rows, clock mapping and unit/orientation conversions in `physical-mapping.json`; missing coverage within that selection remains an input error. Preflight reports resolved bindings and checks CUDA in the selected worker without loading model weights.

This example reuses legacy `pour_tea_io.py` intrinsics, transforms and rotation-vector interpretation. The transforms retain the original approximation and are not independently recalibrated. Gripper geometry uses metres; v62 closure rules retain their original centimetre scale and thresholds. Algorithm bindings cannot promote unknown units, feedback identity or calibration to confirmed status.

```bash
cp configs/robots/x1pro_safety_example.yaml /path/to/robot-safety.yaml
cp src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/pour_tea_task_graph.json /path/to/pour_tea_task_graph.json
```

Edit data and model paths before preparing depth. Existing sensor depth may use `source: sensor_depth` with units and registration evidence. Monocular estimates must not be labelled sensor measurements.

## 4. Optional depth preparation for RGB recordings

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

Set `depth.registration_id` to the value in the correspondence evidence; the generator above uses `same-original-rgb-frame-resized-prediction`. Before initializing safety models, EDC checks evidence completion, episode ID/source path/selector, channel, frame range, units and depth source. It also checks each original frame identity, RGB dimensions and SHA-256, plus the depth file SHA-256. A mismatch or incomplete evidence stops execution and retains diagnostics in `depth-validation.json`. Matching fingerprints do not establish geometric calibration accuracy.

The depth preparation command generates these fingerprints automatically. They bind depth to the images used to prepare it; ordinary changes between video frames are expected. If editing or re-encoding changes decoded RGB pixels, prepare corresponding depth and evidence again. Do not replace hashes merely to accept stale evidence.

Sensor depth must provide the same evidence structure: `status: completed`, `episode_id`, `source_episode`, `selector`, `channel` (resolved EDC channel name), `frame_range`, `source: sensor_depth`, `unit`, `registered_to_rgb: true`, `registration_id`, and the depth file SHA-256 in `depth.files`. The ordered `frames` list contains `reference_index`, `source_index`, `rgb_shape: [H, W]` and `rgb_sha256` for every selected frame. RGB hashes cover contiguous RGB uint8 pixel bytes, not image-file bytes. Registration requires actual calibration, not merely a changed declaration.

## 5. Run and inspect results

```bash
# Edit all /path/to/... values in robot-safety.yaml before running.
edc robot check --robot /path/to/robot-safety.yaml --data /path/to/pourtea-episode
edc run /path/to/pourtea-episode \
  --robot /path/to/robot-safety.yaml \
  --algorithm Collision_Detection_Qwen3vl \
  --output /path/to/new-safety-results
```

The `python` parameter selects the independent worker without manual activation. Set it to the `safety-depth` environment if that is the worker you created. Use a new output directory outside raw data.

Open root `report.html` for a compact pass/fail/review judgment, reason and full-result link. Pass means checks completed for the selected scope without confirmed or unresolved events; it is not safety certification. No numerical score is invented. `catalog.json` indexes records and evidence.

A complete execution requires `records.jsonl` status `completed`, `native-result.json` field `assessment_complete: true`, and all four `checks` statuses `completed`. Confirmed events produce fail; unresolved events produce review. Neither is a processing failure. Check `assessment_scope.original_frame_range`: completion of the example does not establish assessment of the whole recording. Fragment runs store `input_scope.assessment_scopes` and `scope_complete: false` in the standard record. HTML labels completion and verdicts as applying to the fragment, with the whole episode incomplete. Workflows exclude the episode from `value_completed_episodes` until its full scope and required checks are complete.

| Evidence | Contents |
|---|---|
| `input-mapping.json`, `physical-mapping.json` | Original frames/times, view pairing, signal units and conversions |
| `input/video-mapping.json` | Internal 30 FPS presentation video to original-frame mapping; 30 FPS is an encoding parameter |
| `provenance.json`, `worker.log` | Model/source/configuration fingerprints and diagnostics |
| `native/safety_report.json` | Four-event native summary and scan coverage |
| `native/events_adjudicated.json` | Candidates, confirmed/rejected/review judgments and supporting reasons |
| `native/model-requests/`, `native/collision/` | Request images, raw responses, masks, tracks and geometry |
| `depth-validation.json` | Depth-file and per-frame RGB correspondence checks, including mismatch reasons |
| `native-result.json` | Completion, compact judgment and actual scope |

Artifacts live under `_runs/<run_id>/Collision_Detection_Qwen3vl/artifacts/<episode-key>/`. Full review videos are not exported by default. Internal scan videos, cached frames and geometry arrays support execution and traceability; they are not original recordings. Missing inputs/dependencies, inference failures and incomplete checks retain existing evidence. Successful command exit, an HTML file or absent event files alone do not prove completion.

## Sources and scope

Implementation: `src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/`, registered through `AlgorithmSpec + run(context, params)`, returning `edc_record_v1` without research-repository or DQS imports. Candidate extraction and adjudication preserve the frozen v62 method. Detection accuracy needs separate task-specific evaluation.

`SOURCE.json` records original hashes and unresolved source licensing. Dependencies and models retain their respective terms: CoTracker and this DA3 model have non-commercial restrictions; SAM3 has separate terms. EDC packages exclude model weights, raw data, historical outputs and internal audits.
