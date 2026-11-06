# Quick Start

[Home](../../README.md) · [简体中文](QUICKSTART.zh-CN.md)

This is a tutorial: follow the steps in order to produce the first report. Complete descriptions of fields, parameters, and decision rules are in the [reference](../reference/index.en.md); task-oriented procedures are in the [how-to guides](../how-to/index.en.md).

Use a **real X1 Pro dual-arm MCAP recording** to prepare data, inspect recommendations, execute checks, and view results. Replace every `/path/to/...` with a local path; keep configuration separate from raw data.

## 1. Install

```bash
cd /path/to/embodied-data-curation
python -m venv .venv
source .venv/bin/activate
pip install '.[formats]'
```

Python ≥3.10 is required. `pip install .` installs the lightweight core; `formats` supplies format readers and CPU dependencies. The official LeRobot backend additionally needs Python ≥3.12 and `pip install '.[lerobot]'`. Video-container checks require available `ffmpeg`/`ffprobe` executables.

<a id="data"></a>
## 2. Prepare data

A real recording has been copied locally for this example. **The MCAP is not distributed with the repository, and no public download is currently provided.** Place the matching recording as below. Different recordings require field/configuration checks.

```bash
mkdir -p /path/to/dataset
cp /path/to/source/episode.mcap /path/to/dataset/episode.mcap
python examples/quickstart/list_inputs.py /path/to/dataset/episode.mcap
```

| Item | Example |
|---|---|
| Robot/input | X1 Pro, six joints per arm; one MCAP recording |
| Size/duration | 663,706,769 bytes; approximately 48.28 seconds of container time |
| Cameras | Head, chest, left/right wrist RGB, plus head depth |
| Numeric data | Arm joint/end-effector feedback, master commands, and other declared fields |
| Configuration | [`configs/robots/x1pro_mcap.yaml`](../../configs/robots/x1pro_mcap.yaml) |
| Source SHA-256 | `95993aab130d1ffa2ea9cb9367911f8216f90b2268b076136908f7130053b6d2` |

Confirmed joint positions in rad, velocities in rad/s, and end-effector XYZ in m are retained. Unknown physical quantities, some physical-parameter confirmations, and the common clock remain unresolved. This robot card describes the MCAP layout. [`x1pro.yaml`](../../configs/robots/x1pro.yaml) targets a different JSON/MP4 layout and is not interchangeable. The episode boundary follows the data owner's existing declaration, not file counting.

The file is used for local validation. Redistribution of a real example still needs license confirmation; copying preserves source bytes.

## 3. Recommend and execute

Inspect algorithm input eligibility:

```bash
python examples/quickstart/recommend.py /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml
```

Run the nine-method quality collection and generate HTML:

```bash
edc batch /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --output /path/to/results/report.html \
  --keep /path/to/results/details --jobs 1
```

`--keep` is optional and requires a new directory. Select individual methods with `edc run --algorithm ...`; see [algorithm selection](../how-to/RUN_AND_SELECT.md#selection). No algorithm-code changes are needed.

<a id="results"></a>
## 4. View results

Open `/path/to/results/report.html` in a browser. `report.json` contains summary decisions; `details` retains complete outputs, with `catalog.json` locating run records and evidence. Keep outputs separate from original data.

The reference aggregate decision is **review recommended**. These results apply to this recording/configuration, not robot-wide health or other capture batches:

| Algorithm | Result | Summary |
|---|---|---|
| Structure_Check | PASS | Readable structure; missing paired camera_info for four RGB streams is retained as a finding |
| Numeric_Check | REVIEW | Recorded finiteness can be checked; incomplete physical-parameter confirmation is not a complete physical pass |
| Action_Smoothness | PASS | Holding periods checked separately for both arms; movement metrics are informational |
| Check_Timestamp | REVIEW | Per-stream intervals available; cross-stream conditions incomplete |
| Sync_Check | Not applicable | Common clock unconfirmed; acquisition synchronization is not established |
| Root_Cause | Not applicable | Cross-stream time comparison requirements missing |
| Dedup_Check | REVIEW | Some raw channels lack valid time, limiting coverage |
| Reset_Detection | REVIEW | Existing Home calibration yields review; the short summary does not expand deviations, so inspect retained outputs |
| Video_Integrity | PASS | Frame sampling; left/right wrists have 3 and 2 blurred frames respectively out of 290 sampled frames each |

Recommendation, successful execution, and quality passing are distinct. Local reports retain complete records; the guide shows only the necessary summary. [Reading and locating findings](../how-to/RUN_AND_SELECT.md#results)

## Next steps

- Switch to another dataset: first check fields and configuration with [connect data and configure a robot](../how-to/DATA_AND_ROBOT.md).
- Run only some checks or change the scope: [recommend, select, and run algorithms](../how-to/RUN_AND_SELECT.md).
- Look up an algorithm's parameters, rules, and limits: [built-in quality algorithm reference](../reference/ALGORITHMS.md).
