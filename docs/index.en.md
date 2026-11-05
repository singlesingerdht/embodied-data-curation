---
html_theme.sidebar_secondary.remove: true
---

# EDC · Embodied Data Curation

:::{container} edc-intro
Turn usable robot data into experience worth learning from.
:::

**EDC = Embodied Data Curation.** For data users and algorithm developers,
the framework connects data readers, robot configuration, quality checks,
reports, and evidence to make data selection traceable.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Quick start
:link: QUICKSTART
:link-type: doc

Prepare data and configuration, run nine built-in quality checks, and read the report.
:::

:::{grid-item-card} Data and robot configuration
:link: DATA_AND_CONFIGURATION
:link-type: doc

Find supported formats and configure fields, arms, units, clocks, and calibration.
:::

:::{grid-item-card} Recommendation and selection
:link: ALGORITHMS
:link-type: doc

Choose methods for your inputs and understand what each quality check can establish.
:::

:::{grid-item-card} Algorithm development
:link: DEVELOPMENT
:link-type: doc

Add plugins with QualityAlgorithm, AlgorithmSpec, and the shared output interface.
:::

::::

## Framework overview

![EDC overview: quality evidence, task progress, and policy feedback guide data curation](assets/framework-overview.svg)

Data quality checks are available today; task and policy value methods will be integrated progressively.
See [From Quality Checks to Autonomous Data Curation](QUALITY_VALIDATION.md) for research foundations and validation boundaries.

## Run checks and use the results

With a recording and a matching robot configuration:

```bash
edc batch /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --output /path/to/results/report.html --jobs 1
```

Open the report in a browser to inspect conclusions, reasons, and evidence by recording or algorithm.
Use [`edc regenerate`](SELECTION.md) to export selected data. Successful execution,
passing quality checks, and workflow clearance are separate outcomes.
