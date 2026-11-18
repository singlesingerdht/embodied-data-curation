# Initial_State_Distribution

[简体中文](README.zh-CN.md)

Run through EDC `AlgorithmSpec + run(context, params)`. The bundled `SOURCE.json` retains source hashes and unresolved license status. This guide does not assert that code reuse rights, model licenses or data rights have been cleared.

## Run with edc

This guide applies to installations where `edc algorithms` lists `Initial_State_Distribution`. Registration alone does not establish readiness: readable inputs, SAM3 dependencies and local weights are also required. The base installation neither bundles nor downloads models.

### Installation

From the framework source directory, run `python -m pip install '.[formats,initial-state]'` to install readers and Pillow for initial-frame caching. Prepare the model environment separately; the base installation does not introduce Torch, SAM3 or model weights.

A supported dependency combination uses Python 3.12, Torch 2.8.0, TorchVision 0.23.0, Transformers 4.57.1, Tokenizers 0.22.1 and NumPy 1.26.4. Obtain the SAM3 source package and weights separately. Wheel paths below are placeholders; the EDC wheel must match the caller's method source.

```bash
python3.12 -m venv /path/to/sam3-env
/path/to/sam3-env/bin/python -m pip install \
  /path/to/embodied_data_curation.whl /path/to/sam3.whl \
  'torch==2.8.0' 'torchvision==0.23.0' \
  'transformers==4.57.1' 'tokenizers==0.22.1' 'numpy==1.26.4' \
  'Pillow>=11,<12' 'opencv-python-headless==4.11.0.86' \
  'timm>=1.0.17,<2' 'ftfy==6.1.1' 'einops>=0.8,<1' \
  'pycocotools>=2,<3' 'psutil>=5' 'setuptools>=70,<81'
```

Use CUDA for SAM3. Whether `device: cpu` works depends on the upstream SAM3 version; some versions still initialize CUDA tensors. Optional spatial geometry also requires OpenCV and actual calibration. Synthetic geometry tests do not establish a camera's calibration or monocular scale accuracy.

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
