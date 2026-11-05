# Embodied Data Curation Framework

**EDC = Embodied Data Curation**. A data curation framework for robot data users and algorithm developers.

[简体中文](README.zh-CN.md) · [Quick start](docs/QUICKSTART.md) · [Data integration](docs/DATA_AND_CONFIGURATION.md) · [Algorithms](docs/ALGORITHMS.md) · [Development](docs/DEVELOPMENT.md)

## Overview

**Take robot data from “usable” to “worth learning from.”** EDC aims to discover the potential of embodied data and curate reliable, valuable datasets for target tasks and policy models.

![EDC vision: let user goals drive how data evolves, through cross-robot reuse, automation, and verifiable choices](docs/assets/framework-overview.png)

**User needs** include the task and success criteria, target policy, quality requirements, and data/computing budget, with expert demonstrations or reliable references where applicable. Today, users receive quality reports, issue evidence, selected data copies or symlinks, and source manifests. Planned outputs add task annotations and data subsets or improved versions suited to the target policy. [Needs and deliverables](docs/QUALITY_VALIDATION.md#needs)

- **Data quality | Different robots. One approach to quality checks.** Make data from different robots readable and interpretable by machines, with reusable quality checks.
- **Task value | Turn human understanding into machine learning signals.** Draw on expert demonstrations and task requirements to identify key actions and corrective experience worth learning.
- **Policy value | Connect human goals and machine learning through feedback.** Use goals, reference experience, and model training feedback to identify helpful data, reducing repeated real-robot evaluations during selection.

Across these three levels, the framework pursues two further advantages:

- **Automation | Learn to assess from experience; improve choices through feedback.** Learn assessment criteria from expert demonstrations and use data and training feedback to guide selection, progressively reducing manual assessment, rule adjustment, and method selection. [Mechanisms and research evidence](docs/QUALITY_VALIDATION.md#automation)
- **Evidence | Trace the basis of each choice and test its effects.** Connect selection to quality evidence, task progress, and model learning signals; record processing steps and validate choices with independent feedback. [Verifying choices](docs/QUALITY_VALIDATION.md#evidence)

> Data quality checks are available today. Task-value and policy-value methods have research foundations and will be integrated progressively.

> **Long-term goal: you set the objective; the framework keeps exploring autonomously—which data to keep, which experience to learn from, and what to improve next.**

See [From Quality Checks to Autonomous Data Curation](docs/QUALITY_VALIDATION.md) for the three levels, research foundations, and validation plans, or the [presentation (Chinese)](docs/presentations/edc-framework.zh-CN.pptx). Readers cover LeRobot, HDF5, Parquet, ROS bags, MCAP, and more; see [supported formats](docs/DATA_AND_CONFIGURATION.md#formats) for conditions.

## What's New

> 🚀 The Embodied Data Curation Framework repository is coming soon. Stay tuned!

## Architecture and Modules

![EDC architecture](docs/assets/architecture.svg)

- **Data users**: provide data and robot configuration → review recommendations and select algorithms → run, inspect reports, and export data.
- **Algorithm developers**: write algorithms and declare inputs/outputs → register and validate → join the same recommendation and execution flow.

Both paths share input checks, execution, and result recording. [Modules and extensions](docs/DEVELOPMENT.md#interfaces)

## Installation and Quick Start

Python 3.10 or later is required. Run from a repository checkout and replace `/path/to/...` with local paths.

**1. Install**

```bash
cd /path/to/embodied-data-curation
python -m venv .venv
source .venv/bin/activate
pip install '.[formats]'
```

**2. Prepare data**

Place a real X1 Pro dual-arm MCAP recording matching [`x1pro_mcap.yaml`](configs/robots/x1pro_mcap.yaml) at `/path/to/dataset/episode.mcap`. [Sample details and availability](docs/QUICKSTART.md#data)

**3. Run and view results**

```bash
edc batch /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --output /path/to/results/report.html --jobs 1
```

Open `/path/to/results/report.html` in a browser; `report.json` is saved alongside it. Successful execution does not mean all data passed. [Full quick start](docs/QUICKSTART.md)

**4. Export selected data (optional)**

Export from the batch `report.json`; only records marked for retention (`keep`) are selected by default. Choose one mode:

```bash
# Copy data
edc regenerate /path/to/results/report.json --output /path/to/selected --mode copy

# Or create symbolic links to the original data
edc regenerate /path/to/results/report.json --output /path/to/selected-links --mode link
```

Output includes copies or links and a source manifest, `manifest.json`. Originals stay unchanged; links require the sources to remain available. [Export and limitations](docs/SELECTION.md)

## Data and Configuration

Dataset information describes file locations, tasks, and operation boundaries; robot configuration defines field meanings; algorithm parameters set the scope and criteria for this run.

**Robot configuration declares which arms exist; algorithm configuration selects which arms to inspect.** `arms: all` checks all declared arms, `[left]`/`[right]` selects one side, and `main` can represent an independent single arm. Reuse one robot configuration across tasks.

| Task | Documentation |
|---|---|
| Inspect fields, formats, and operation boundaries | [Dataset integration](docs/DATA_AND_CONFIGURATION.md#dataset) |
| Map source fields and configure one or two arms | [Robot configuration](docs/DATA_AND_CONFIGURATION.md#robot) |
| Confirm units, time sources, and clocks | [Units and time](docs/DATA_AND_CONFIGURATION.md#units-time) |
| Inspect URDF joints and validate configuration | [URDF usage and limits](docs/DATA_AND_CONFIGURATION.md#urdf) |
| Set algorithm inputs, scope, and parameters | [Algorithm configuration](docs/ALGORITHMS.md#selection) |

Values with unknown units can be retained and checked for NaN/Inf, but cannot support physical calculations requiring known units.

## Algorithm Recommendation and Execution

Recommendations list **fully available, partly available, and unavailable** algorithms, with the scope they can check and missing requirements. Execution determines whether data passes.

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

`run` preserves the specified order; without a selection, it attempts fully and partly available quality methods and records missing requirements. `batch` runs the quality collection on one recording, several recordings, or a directory. `edc algorithms` only lists registered algorithms. [Selection and Python API](docs/ALGORITHMS.md#selection)

The example writes `/path/to/results/selected/report.html`, using the same four sections as batch. `--no-html` disables single-run HTML while preserving JSON and evidence. Batch produces one combined HTML report and a `report.json` for export; `--keep` retains full per-recording results. [Reports and evidence](docs/ALGORITHMS.md#results)

| Algorithm | Checks | Minimum input | Details |
|---|---|---|---|
| `Structure_Check` | Channels, cameras, and duration | Channel list and structural requirements | [Structure](docs/ALGORITHMS.md#structure-check) |
| `Numeric_Check` | Finite values and configured physical limits | Numeric fields; physical checks also need units and limits | [Numeric validity](docs/ALGORITHMS.md#numeric-check) |
| `Action_Smoothness` | End-effector vibration while holding still and stuck readings | XYZ feedback and a usable timeline | [Smoothness](docs/ALGORITHMS.md#action-smoothness) |
| `Check_Timestamp` | Intervals, pairing, and gaps | Valid timestamps with known units | [Timestamps](docs/ALGORITHMS.md#check-timestamp) |
| `Sync_Check` | Recording spans and record–acquisition time differences | Independent acquisition times and comparable clocks; differences also need record times | [Synchronization](docs/ALGORITHMS.md#sync-check) |
| `Root_Cause` | Timing anomalies across streams | Comparable timelines and source groups | [Timing attribution](docs/ALGORITHMS.md#root-cause) |
| `Dedup_Check` | Repeated timestamps or sequence numbers | Original times or sequence information | [Duplicates](docs/ALGORITHMS.md#dedup-check) |
| `Reset_Detection` | Distance from Home at the start and end | Position feedback, Home, and tolerances | [Reset](docs/ALGORITHMS.md#reset-detection) |
| `Video_Integrity` | Image content and video integrity | Decodable frames or video | [Images and video](docs/ALGORITHMS.md#video-integrity) |

Each algorithm retains its own metrics and evidence, without a combined score. [Check rationale and research directions](docs/QUALITY_VALIDATION.md)

## Developers and Project Structure

Quality algorithms can inherit `QualityAlgorithm`, return a list of `Item` objects, and use `AlgorithmResult` to aggregate them. General plugins use `AlgorithmSpec + run(context, params)`; registration connects them to the same input checks and execution flow.

[Output classes and adding a new algorithm](docs/DEVELOPMENT.md#quality-example) · [Reader plugins](docs/DEVELOPMENT.md#readers) · [Contributing (Chinese)](CONTRIBUTING.md)

```text
src/edc/
├── data/          # Data interfaces, readers, and transforms
├── robots/        # Configuration, parts, and channel meanings
├── algorithms/    # Registration, preflight, nine quality methods, and output classes
├── execution/     # CLI, single-run, and batch execution
├── workflows/     # Optional stages and quality recheck interfaces
└── reporting/     # Reports and evidence
configs/
├── robots/        # Robot YAML and associated URDF
└── templates/     # Single-arm, dual-arm, and other templates
examples/          # Usage and plugin examples
docs/              # Topic guides
tests/             # Contract and implementation tests
```

### Roadmap

These are planned integrations or research directions. See the [quality guide](docs/QUALITY_VALIDATION.md#layers) for methods and foundations:

1. Task segmentation, progress assessment, and useful segment discovery: STEAM and MGT-QV.
2. Automatic annotation against task criteria and multimodal review: drawing on external work such as FLORA.
3. Data editing, demonstration resampling, and quality rechecks: directions such as DemoTempo.
4. Selection for target policies: RoboDrop and VLA as a judge.
5. Feedback-driven rules and algorithm combinations, then new algorithm generation, validation, and integration toward [autonomous curation](docs/QUALITY_VALIDATION.md#autonomous).
