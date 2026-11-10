# Algorithm Integration and Internalization

[简体中文](ALGORITHM_INTEGRATION.zh-CN.md) · [Plugin development](ALGORITHM_PLUGIN.md) · [Output contract](../reference/OUTPUTS.md)

This guide explains how to integrate an algorithm as an EDC plugin and bring its implementation
into the framework. Contributions include code, input/output documentation, dependencies and
provenance, usage examples and tests.

## 1. Choose a category and code location

Category describes purpose; built-in or plugin describes code ownership and loading. External
algorithms first arrive as plugins and may be internalized after validation or remain independently
maintained. Internal development may start in its category directory with equivalent validation.

| category | Purpose | Required outputs |
|---|---|---|
| quality | Check defects and fitness for stated requirements | Native metrics, verdicts, coverage and evidence |
| value | Assess coverage, distribution, contribution or experiment-specific value | Native statistics, events, scores or estimates and their experimental scope |
| editing | Repair, trim or adjust existing data | New data, params and source_mapping |
| augmentation | Add samples or create variations | New samples, params and source_mapping |

The directory convention for built-in algorithms is relative to the repository root:

```text
src/edc/algorithms/
├── registry.py
├── contracts.py
├── quality/algorithms/<Algorithm_Name>/
├── value/algorithms/<Algorithm_Name>/
├── augmentation/algorithms/<Algorithm_Name>/
└── editing/algorithms/<Algorithm_Name>/
```

**Current loading support:** Core discovery loads nine built-in quality algorithms through the
existing quality adapter. Unified automatic discovery across these four directories is not yet
available. Other categories can use the plugin interface; creating a directory alone does not
register an algorithm or establish support.

Use `algorithm.py` for lightweight declarations and execution entry points. Organize computation
in files such as `method.py`, `extractor.py` and `metrics.py` as needed. Include `README.md` and
traceable `SOURCE.json` with authorship, source, licenses, inputs, outputs, parameters, dependencies
and limitations. Extract category `shared/` code only when reused. Use the existing framework
facilities for reading data, execution and workflows.

## 2. Implement the common contract

Built-ins and plugins share `AlgorithmSpec + run(context, params)`. The framework handles
preflight, parameter validation and result storage. Installed plugins use the `edc.algorithms`
entry point; the `EDC_PLUGINS` folder mechanism follows the same external-plugin validation.
See [plugin development](ALGORITHM_PLUGIN.md) for a runnable example.

- Built-in IDs match directory names; external IDs use `team__algorithm`. IDs are globally unique.
  Duplicates, incompatible interfaces and invalid declarations fail explicitly without silent replacement.
- Granularity is `episode` or `dataset`. Dataset algorithms implement
  `preflight_dataset(dataset, params)` and execute once for the selected dataset. Use
  `episode_requirements/preflight_episode` for per-episode screening; do not fill declarations
  supported only for episode algorithms.
- Declaration and discovery must not import model libraries, initialize models or download weights.
  Check dependencies and devices during preflight and load models during execution.
- Use Dataset → Episode → Channel and retain stable IDs, source, selector, units, time sources and
  clocks. Inputs are read-only; write outputs to `context.workdir`, without caches beside source data.
- Preserve native results according to the [output contract](../reference/OUTPUTS.md). Processing
  failures are separate from data judgments. Value scores do not automatically produce quality
  verdicts; do not aggregate scores across algorithms.
- Internal code imports its owning modules directly; plugins use public types exported by `edc`.

## 3. Internalize a plugin

1. **Prepare a comparison baseline.** Fix source, effective parameters, inputs and selectors,
   dependencies and weights. Include file hashes for uncommitted changes; a commit alone may not
   identify the working implementation. Preserve baseline results read-only.
2. **Choose the destination and versions.** Select the category directory and old/new IDs.
   Track method, interface and package versions separately. Relocation alone preserves method and
   interface versions; changes to computation, thresholds or clock behavior need separate review and validation.
3. **Move one implementation.** Include methods, adapters, provenance, required resources and tests.
   Remove dependencies on research-environment paths and connect shared registration. If a category
   lacks built-in discovery, add and validate that support first. Do not add algorithm-specific ID
   dispatch branches to the runner.
4. **Update active callers.** Update parameter keys, workflows, robot checks, examples and tests.
   Preserve quality presets and report ordering; internalizing value algorithms does not add them
   to quality presets. Never rename historical runs or catalogs.
5. **Retire the old entry point.** Disable only the migrated algorithm in the target environment,
   preserving other algorithms in a shared plugin. Detect residual old entries even when IDs differ.
   Two executable implementations fail switch acceptance; do not add silent aliases.
6. **Validate and update usage.** Check packages and fresh runs, then document available entry
   points, parameters, dependencies and limitations. Use separate output directories for comparisons
   and preserve historical results.

## 4. Validate integration and internalization

- **Contracts and failures:** Test discovery after installation, successful runs, missing inputs
  and dependencies, bad samples, partial failures, duplicate IDs and incompatible interfaces.
  Plugin import failures retain diagnostics without blocking other plugins; a missing target still fails integration.
- **Equivalent results:** Compare identical inputs, selectors, parameters, weights, seeds and
  dependencies. Check stable IDs, channel/time provenance, native scores/events/distributions,
  completeness and failures. Deterministic results match field by field. Set floating-point or
  model nondeterminism tolerances before running, without relaxing them to pass. List permitted
  differences in run IDs, timing, output locations and intended algorithm IDs beforehand.
- **Models and scope:** Fixed arrays and inference substitutes check contracts and statistics.
  Validate real models separately; interface checks do not establish detection accuracy,
  multi-dataset applicability or training benefits. Mark incomplete or unchecked assessments explicitly.
- **Distributions:** Include methods and SOURCE resources in wheel/sdist. Install outside the
  source tree and check actual module locations, unique registration and minimal execution.
  Base installation does not require model/GPU dependencies; report missing execution dependencies explicitly.
- **Editing and augmentation:** Validate output, params, source_mapping and quality rechecks of
  derived data. The current generic workflow supports only identity mappings with unchanged rows
  and times; cropping, resampling and new synthetic episodes need separate implementation and validation.
- **Provenance and licenses:** Check algorithm code, third-party libraries, models and data
  separately. Unresolved licensing does not establish permission to publish.

## 5. Recover from a failed switch

Keep diagnostics and restore the matching plugin, configuration and dependency environment. Reverse
only changes from this migration. Preserve old and new results separately; never relabel historical
catalogs as fresh runs or overwrite raw inputs or unrelated development work.

After successful revalidation, update usage so documented entry points match the implementations
actually discovered from installed packages.
