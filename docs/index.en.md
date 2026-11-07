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

## Framework overview

:::{figure} assets/framework-overview.svg
:alt: EDC overview: quality evidence, task progress, and policy feedback guide data curation
:target: _images/framework-overview.svg
:figclass: edc-overview

Click the image to view it at full size.
:::

- **Cross-robot reuse**: Configuration maps each robot's fields, units, and components to shared inputs, so the same checks work across robot types.
- **Automation**: Learn assessment criteria from demonstrations and improve selection through data and training feedback, progressively reducing manual judgment and rule tuning.
- **Evidence**: Connect each selection decision to quality evidence, task progress, and model learning signals, making its basis traceable and its effects verifiable.

Data quality checks and selection/export are available today; task-value and policy-value methods will be integrated progressively.
[From Quality Checks to Autonomous Data Curation](explanation/QUALITY_VALIDATION.md).

## Start here

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} Data users

Check data, inspect issues and evidence, and export selected recordings.

[Quick Start](tutorial/QUICKSTART.md) · [Data and Robot Configuration](how-to/DATA_AND_ROBOT.md)

[Run Algorithms](how-to/RUN_AND_SELECT.md) · [Selection and Export](how-to/SELECTION.en.md)
:::

:::{grid-item-card} Algorithm developers

Reuse data reading, checks, and reports to add algorithms or data formats.

[Algorithm Plugins](how-to/ALGORITHM_PLUGIN.md) · [Reader Plugins](how-to/READER_PLUGIN.md)

[Interfaces and Outputs](reference/OUTPUTS.md)
:::

::::

## Browse the documentation

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Tutorial
:link: tutorial/QUICKSTART
:link-type: doc

Install, prepare data, and generate your first report.
:::

:::{grid-item-card} How-to guides
:link: how-to/index.en
:link-type: doc

Connect data, run algorithms, export data, write plugins.
:::

:::{grid-item-card} Reference
:link: reference/index.en
:link-type: doc

Look up data formats, configuration fields, parameters, and interfaces.
:::

:::{grid-item-card} Explanation
:link: explanation/index.en
:link-type: doc

Understand framework design, data quality, research, and validation.
:::

::::
