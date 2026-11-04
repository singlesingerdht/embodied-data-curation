# Embodied Data Curation Framework

**EDC = Embodied Data Curation**. Read robot data, choose suitable algorithms, and inspect the results. Developers can also add their own algorithms.

[简体中文](README.zh-CN.md) · [Quick start](docs/QUICKSTART.md) · [Data integration](docs/DATA_AND_CONFIGURATION.md) · [Algorithms](docs/ALGORITHMS.md) · [Development](docs/DEVELOPMENT.md)

## Overview

Provide a data path and robot configuration. EDC lists available algorithms and produces a web report and JSON results after execution. The current focus is **reading robot data in different formats and providing nine CPU quality checks**.

| Capability | Current support |
|---|---|
| Data reading | Read joint, camera, and other data from each robot operation, and locate detected problems in the corresponding records and original files |
| Robot configuration | Specify which arm or camera each recording belongs to, its units, and whether it is a command or measured feedback |
| Algorithm selection | List what can run, what can only be checked in part, and which data or settings are missing |
| Quality checks | Check missing data, unusual numbers and timing, vibration while holding still, reset positions, and images/videos |
| Results | Inspect conclusions and problem locations in a web report; save measurements and references to original records in JSON |
| Selected data export | Copy or symlink data marked for retention in a batch report, with a manifest mapping original data to exported data |

Readers cover **LeRobot, HDF5, Parquet, ROS bags, MCAP, and more**. See [supported formats](docs/DATA_AND_CONFIGURATION.md#formats) for backends and message types.

## What's New

> 🚀 The Embodied Data Curation Framework repository is coming soon. Stay tuned!

## Architecture and Modules

![EDC architecture](docs/assets/architecture.svg)

The framework serves two groups:

| Audience | How to use the framework | Get started |
|---|---|---|
| **Data users** | Provide data and configuration → Review available algorithms and missing requirements → Choose algorithms and parameters → Run and inspect reports | [Quick start](docs/QUICKSTART.md) · [Data configuration](docs/DATA_AND_CONFIGURATION.md) |
| **Algorithm developers** | Write an algorithm → Declare required data, parameters, and outputs → Register and validate it → Make it available for recommendation and execution | [Algorithm development guide](docs/DEVELOPMENT.md) |

Both paths share algorithm recommendation, execution, and result recording. [Modules and extensions](docs/DEVELOPMENT.md#interfaces)

## Installation and Quick Start

Python 3.10 or later is required. Run these commands from a local repository checkout; replace `/path/to/...` with your own paths.

**1. Install**

```bash
cd /path/to/embodied-data-curation
python -m venv .venv
source .venv/bin/activate
pip install '.[formats]'
```

**2. Prepare data**

This example uses an MCAP recording collected from an X1 Pro dual-arm robot with [`x1pro_mcap.yaml`](configs/robots/x1pro_mcap.yaml). Place the recording at `/path/to/dataset/episode.mcap`; data names in the configuration must match the file. [Sample details and availability](docs/QUICKSTART.md#data)

**3. Run and view results**

```bash
edc batch /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --output /path/to/results/report.html --jobs 1
```

Open `report.html` in a browser. The accompanying `report.json` provides machine-readable results. Successful execution does not mean all data passed. [Full quick start and reference results](docs/QUICKSTART.md)

**4. Export selected data (optional)**

Use the batch `report.json` to export only data marked for retention (`keep`) by default. Choose one mode:

```bash
# Copy data
edc regenerate /path/to/results/report.json --output /path/to/selected --mode copy

# Or create symbolic links to the original data
edc regenerate /path/to/results/report.json --output /path/to/selected-links --mode link
```

The output directory contains copies or links and a `manifest.json` recording sources and selection criteria. Original data stays unchanged; symbolic links require the sources to remain available. See [selection and export](docs/SELECTION.md) for limits when selecting episodes from shared files.

## Data and Configuration

Configuration describes where the data is stored, which task was recorded, and where each operation starts and ends; which records contain joints, cameras, or other sensors and what units they use; and which algorithms, robot parts, and parameters to use for this run.

**Robot configuration declares which arms exist; algorithm configuration selects which arms to inspect.** `arms: all` selects all declared arms. Use `[left]` or `[right]` for one side of a dual-arm robot, or declare `main` for an independent single arm. The same robot configuration can be reused across task datasets.

| Task | Documentation |
|---|---|
| Inspect available data and specify where each operation starts and ends | [Dataset integration](docs/DATA_AND_CONFIGURATION.md#dataset) |
| Configure one or two arms and their joint, camera, and other recordings | [Robot configuration](docs/DATA_AND_CONFIGURATION.md#robot) |
| Specify units, how time was recorded, and whether device clocks can be compared | [Units and time](docs/DATA_AND_CONFIGURATION.md#units-time) |
| Obtain joint information from URDF and validate mappings | [URDF usage and limits](docs/DATA_AND_CONFIGURATION.md#urdf) |
| Configure algorithm inputs, scope, and parameters | [Algorithm configuration](docs/ALGORITHMS.md#selection) |

Numbers with unknown units can be retained as described in the guide. For example, they can be checked for NaN or infinity, but joint speed cannot be compared against a physical limit until its units are known.

## Algorithm Recommendation and Execution

Recommendations tell you which checks your data can support. **Available** means the required data and settings are present; **partial** explains what can and cannot be checked; **unavailable** lists missing data, settings, or software. Whether the data passes is determined by the execution results.

```bash
# Data-specific recommendations: this script calls edc.recommend()
python examples/quickstart/recommend.py /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml

# Select algorithms and execute in the specified order
edc run /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --algorithm Structure_Check --algorithm Numeric_Check \
  --output /path/to/results/selected
```

Without an explicit selection, execution runs quality algorithms that are fully or partly available and explains what cannot be checked. Use `batch` for batch quality checks, or `run` and the Python API to select individual algorithms. `edc algorithms` only lists installed algorithms. [Selection, Python API, and batch execution](docs/ALGORITHMS.md#selection)

`run` writes `report.html` in its output directory by default, using the same four report sections as `batch` and showing only the selected algorithms. For the example above, open `/path/to/results/selected/report.html`. Full JSON records and evidence are stored under `_runs/<run_id>/` and located through `catalog.json`; each run also retains its own report there. Add `--no-html` to skip HTML generation while keeping JSON and evidence, or use `edc.run(..., html_report=False)` in Python.

`batch` accepts a single recording, multiple recordings, or a directory. It disables HTML generation in its internal `run` calls and produces one combined report. Even with `--keep`, per-episode output retains JSON and evidence without extra HTML reports. Its `report.json` can be used for the export commands above.

| Algorithm | Checks | Minimum input | Details |
|---|---|---|---|
| `Structure_Check` | Missing required data and insufficient duration | Available data list and configured requirements | [Structure](docs/ALGORITHMS.md#structure-check) |
| `Numeric_Check` | NaN, infinity, and values outside configured limits | Numeric data; limit checks also need units and bounds | [Numeric validity](docs/ALGORITHMS.md#numeric-check) |
| `Action_Smoothness` | End-effector vibration while holding still and stuck position readings | XYZ feedback, or positions calculated from joints as configured, with timestamps | [Smoothness](docs/ALGORITHMS.md#action-smoothness) |
| `Check_Timestamp` | Unusual recording intervals and gaps | Timestamps and their units | [Timestamps](docs/ALGORITHMS.md#check-timestamp) |
| `Sync_Check` | Differences between device acquisition times | Each stream's acquisition times and a confirmed relationship between device clocks | [Synchronization](docs/ALGORITHMS.md#sync-check) |
| `Root_Cause` | Which recordings have timing problems at the same time | Comparable timestamps and device groups | [Timing attribution](docs/ALGORITHMS.md#root-cause) |
| `Dedup_Check` | Repeated timestamps or record numbers | Original timestamps or sequence numbers | [Duplicates](docs/ALGORITHMS.md#dedup-check) |
| `Reset_Detection` | Whether the robot starts and ends at the specified position | Actual positions, reference positions, and allowed error | [Reset](docs/ALGORITHMS.md#reset-detection) |
| `Video_Integrity` | Black frames, blur, suspected frozen images, and damaged video files | Readable images or video | [Images and video](docs/ALGORITHMS.md#video-integrity) |

Each algorithm reports its own results and supporting evidence. Results are not combined into one overall score. [Reading reports](docs/ALGORITHMS.md#results)

## Developers and Project Structure

Follow the examples to write an algorithm, declare its required data and parameters, and return conclusions, reasons, and problem locations. Quality algorithms can use the existing output base classes. After registration, the framework runs the algorithm and generates reports. Other algorithm categories use the [general plugin interface](docs/DEVELOPMENT.md#interfaces).

[Output classes and a complete algorithm example](docs/DEVELOPMENT.md#quality-example) · [Reader plugins](docs/DEVELOPMENT.md#readers) · [Contributing (Chinese)](CONTRIBUTING.md)

```text
src/edc/
├── data/          # Read data formats and handle units and time
├── robots/        # Read robot configuration and associate parts with data
├── algorithms/    # Register algorithms, check inputs, quality methods and output classes
├── execution/     # CLI, single-run, and batch execution
├── workflows/     # Run algorithms in stages and repeat quality checks
└── reporting/     # Reports and evidence
configs/
├── robots/        # Robot YAML and associated URDF
└── templates/     # Single-arm, dual-arm, and other templates
examples/          # Usage and plugin examples
docs/              # Topic guides
tests/             # Functionality and interface tests
```

### Roadmap

- Add value assessment algorithms and select operation recordings that meet each algorithm's requirements.
- Add editing and augmentation algorithms, then recheck the quality of modified or generated data copies.
- Extend examples that run algorithms in sequence; [workflow interfaces and synthetic examples (Chinese)](docs/WORKFLOWS.md) already exist, with the algorithms above and full-flow validation to follow.
- Add research and GPU algorithms as needed, documenting required software, hardware, and licenses.
