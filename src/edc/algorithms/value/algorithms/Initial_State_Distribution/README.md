# Initial_State_Distribution

[简体中文](README.zh-CN.md)

Run through EDC `AlgorithmSpec + run(context, params)`. The bundled `SOURCE.json` retains source hashes and unresolved license status. This guide does not assert that code reuse rights, model licenses or data rights have been cleared.

## Run with edc

This guide applies to installations where `edc algorithms` lists `Initial_State_Distribution`. Registration alone does not establish readiness: readable inputs, SAM3 dependencies and local weights are also required. The base installation neither bundles nor downloads models.

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

Copy the [initial-state configuration](../../../../../../configs/robots/initial_state_example.yaml) from the source repository and set `checkpoint`, `python` and the camera channel. Select episodes with comparable viewpoints and workspaces, explicitly listing them in `dataset.yaml` at the dataset root:

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


Before model initialization, provenance.json records model/source/configuration evidence. Failures retain existing artifacts. Native worker methods require the exact same EDC package in their configured Python environment.

Optional `python` selects an isolated SAM3 interpreter; `timeout_seconds` bounds worker execution. Omitting `python` keeps in-process execution. EDC prepares initial RGB frames and source mappings; both paths share the same detection and statistics implementation.

## HTML result report

`edc run` writes a compact `report.html`: algorithm, object, native score, judgment, execution status, a short reason and detailed-result links. Pass/fail judgments come from the method; scores without pass thresholds have no assigned judgment, and insufficient evidence remains incomplete. Plots, heatmaps, videos and candidate details stay in the native output rather than expanding in HTML. Reports do not generate extra videos. `catalog.json` links each run's HTML; `--no-html` disables HTML while retaining JSON and evidence. Share the full output directory to preserve relative links.
