# Framework Structure and Design Choices

[Home](../../README.md) · [简体中文](ARCHITECTURE.zh-CN.md) · [Explanation area](index.en.md)

EDC combines a shared data model, declarative configuration, evidence records, and plugin interfaces.
For procedures see the [how-to guides](../how-to/index.en.md); for fields, parameters, and interfaces see the [reference](../reference/index.en.md); for research foundations and validation boundaries see [from quality checks to autonomous data curation](QUALITY_VALIDATION.md).

## Shared execution pipeline

- **Data users** provide data and a robot configuration, inspect recommendations, choose algorithms, run checks, read reports, and export data.
- **Algorithm developers** write algorithms with declared inputs and outputs, register and validate them, and join the same recommendation and execution flow.

Built-in and external algorithms share input preflight, execution, and evidence records. Public APIs are exported from `edc`; algorithms and readers register through plugin entry points.

```text
src/edc/
├── data/          # data interfaces, readers, and transforms
├── robots/        # configuration compilation, parts, and channel semantics
├── algorithms/    # registry, preflight, nine quality methods, output base classes
├── execution/     # CLI, single and batch execution
├── workflows/     # optional stage orchestration and recheck interfaces
└── reporting/     # reports and evidence
```

## Dataset → Episode → Channel

A recording usually contains several operations; each operation contains joint, camera, and other streams. The framework calls these **Dataset** (the collection), **Episode** (one operation), and **Channel** (one recorded stream).

Episode boundaries come from metadata or explicit mappings. Stable IDs, source files, and selectors preserve source identity across recommendation, execution, reporting, and export. File count does not determine episode count.

## Four configuration types

| Configuration | Answers | Examples |
|---|---|---|
| Dataset | Which operations this collection contains, and where they begin and end | `dataset.yaml`, LeRobot metadata, HDF5 groups |
| Robot YAML | Which part a recording belongs to, what it represents, and its units | `arms`, `cameras`, `data.time` |
| Algorithm parameters | Which scope and thresholds this run uses | `checks.<algorithm_id>`, `arms: [left]` |
| Workflow | Which stages run and how results are rechecked | Quality, value, editing, and augmentation stages |

The top-level configuration references all four types and can be exported as one effective configuration.

**The declared robot and the run scope are separate.** `arms` says how many arms the robot has; `checks.<method>.arms` selects which arms this run checks. `all` expands only declared arm parts, and left/right never fall back to each other. One robot configuration can serve several tasks; tasks and episode metadata belong to the dataset. After a capture batch changes, source mapping, units, calibration, and clock applicability must be rechecked.

## Units and time provenance

Confirmed physical fields are read in SI only **inside the official executor's algorithm context**; source arrays remain unchanged. Unknown units stay unknown: they cannot be replaced by dimensionless `1` or inferred from normalization. Standalone source reads from `open_dataset()` are not universally SI numeric views; plugins should use the executor-provided episode.

Time distinguishes acquisition, container, playback, synthetic, and unknown. Cross-channel comparison requires a common clock or an explicit mapping; a file format, shared row timeline, or chosen reference camera cannot establish acquisition synchronization on its own.

## Result semantics and evidence

Within one run, processing state and data judgment are separate:

- Processing state says whether a check executed and whether it applies.
- Data judgment uses PASS, REVIEW, SALVAGE, or EXCLUDE.
- Unchecked, inapplicable, or failed items cannot become passes.
- Every item keeps native metrics, events, source rows, and evidence; there is no cross-algorithm or cross-arm score.

Evidence supports later review and export. PASS applies to the checked conditions; policy-learning benefits require training evaluation. Run outputs are discovered through `catalog.json`; `_runs/<run_id>` preserves configuration, versions, records, errors, and evidence. Workflows also retain the effective configuration and source fingerprints. Historical evidence remains unchanged.

## Finite-stage workflows

Workflows orchestrate an explicit, finite stage list using SI execution inputs, the algorithm registry, applicability checks, and native results. Editing and augmentation create new versions that require consistency and provenance validation followed by quality rechecks. Outputs that fail validation or quality requirements are suspended. Optional value, editing, and augmentation stages may be omitted; recovery resumes the finite plan.

## Extension points

New algorithms register through `edc.algorithms` using `AlgorithmSpec` and `run(context, params)`; new formats register through `edc.readers`. External IDs use `team__algorithm`; duplicate IDs and incompatible interface versions fail explicitly. Model and GPU dependencies load lazily.

See [add a new quality algorithm](../how-to/ALGORITHM_PLUGIN.md) and [add a reader plugin](../how-to/READER_PLUGIN.md); object definitions are in the [output objects and interface reference](../reference/OUTPUTS.md).

## Explicit limits

- Structure checks do not establish physical semantics, task success, or sensor health.
- Recommendations describe input conditions, not the best algorithm for the user's goal.
- Processing failure records an error without assigning a quality verdict.
- Task-value and policy-value methods are still being integrated; what ships today is data-quality checking, evidence records, and selection/export.

## Related pages

- [From quality checks to autonomous data curation](QUALITY_VALIDATION.md): the three value levels, research foundations, and validation plans.
- [Data formats and robot configuration reference](../reference/DATA_AND_ROBOTS.md): fields, units, and time declarations.
- [Orchestrate and run a quality workflow](../how-to/WORKFLOWS.en.md): stages, rechecks, and suspension rules.
