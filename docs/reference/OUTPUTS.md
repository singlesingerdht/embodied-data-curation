# Interfaces and Outputs

[Home](../../README.md) · [简体中文](OUTPUTS.zh-CN.md) · [Algorithm Plugins](../how-to/ALGORITHM_PLUGIN.md)

Public interfaces for algorithm inputs, quality results, plugins, and run records.
For the procedure of writing a plugin, see [Algorithm Plugins](../how-to/ALGORITHM_PLUGIN.md); for the reasoning behind these boundaries, see [Framework Design](../explanation/ARCHITECTURE.md).

<a id="interfaces"></a>
## Interface types

| Interface | Use | Implement |
|---|---|---|
| `QualityAlgorithm` | Per-episode quality items | `spec` and `check(episode, params) -> list[Item]` |
| `AlgorithmSpec + run(context, params)` | General quality, value, editing, and augmentation plugins | Metadata, input/parameter requirements, and `run`; dataset methods also need `preflight_dataset` |
| Reader plugin | A new data format | `can_open()` and `open_dataset()` returning the common data model |

Import public types from `edc`, including `AlgorithmSpec`, `QualityAlgorithm`, `Item`, and `AlgorithmResult`. Readers, robot configuration, SI execution views, and preflight jointly prepare inputs with declared units and semantics.

<a id="context"></a>
## The data an algorithm receives

The `episode` an algorithm receives is addressed by robot-yaml channel names:

| Member | Provides |
|---|---|
| `channels` | Type, fields, timeline, and clock per channel |
| `read_signal(channel, field)` | SI arrays for confirmed physical fields in the executor-provided episode |
| `read_values` / `iter_frames` | Rows and frames |
| `profile` | The robot yaml |
| `raw(channel)` | Read-only source record: raw integer times, sequence numbers, message type |

<a id="outputs"></a>
## Quality output objects

| Object | Responsibility |
|---|---|
| `Item` | One target: execution `status`, quality `verdict`, short `reason`, `metrics`, `evidence`, and a `remedy` for salvage |
| `AlgorithmResult` | Items and a short note for one method; computed `level`; `to_dict()` / `from_dict()` serialization |
| `QualityAlgorithm` | Calls `check()`, validates a nonempty Item list, and stores a report per Item |

```python
from edc import Item, AlgorithmResult

items = [Item('left arm', verdict='REVIEW', reason='Joint step exceeds the declared threshold.',
              metrics={'max_step_rad': 0.3}, evidence=[{'row': 12}])]
payload = AlgorithmResult('team_example__joint_jump', items).to_dict()
```

- `status='checked'` requires `PASS`, `REVIEW`, `SALVAGE`, or `EXCLUDE`.
- `status='not_applicable'` or `'failed'` requires a reason and no verdict. Unchecked items cannot pass. The executor records uncaught exceptions as processing errors.
- Every non-pass item needs a reason; SALVAGE also needs a remedy. Keep reasons short and put measurements and source rows in their respective fields.
- Do not create cross-algorithm scores. Run records use the `edc_record_v1` schema.

<a id="plugins"></a>
## General plugins and context

Plugins register under `edc.algorithms`. `AlgorithmSpec` declares ID, version, category, granularity, input conditions, parameter schemas, dependencies, and device; categories are quality, value, editing, and augmentation. External IDs use `team__algorithm`; duplicate IDs and incompatible interfaces fail explicitly. Load model/GPU dependencies lazily.

`run(context, params)` reads the current episode or dataset. Workflows expose upstream records through read-only `context.upstream`. A dataset method runs once on the current collection and checks collection requirements in `preflight_dataset()`.

The quality helper does not force value methods to return quality judgments or collapse distributions and experimental estimates into a score. Editing/augmentation returns `output / params / source_mapping`; validate derived copies before quality rechecks, and new data must not overwrite sources. The current transform validator accepts only identity mappings that keep row counts and times unchanged; resampling, arbitrary segment edits, and channel additions/removals are currently unsupported. Every editing/augmentation output branch must be followed immediately by a quality stage, or configuration validation fails.

[Synthetic plugins](../../examples/plugins/workflow/) illustrate the supported contracts.

## Run artifacts

`catalog.json` indexes outputs. Each `_runs/<run_id>` stores configuration, versions, records, errors, and evidence. Workflows also store the complete effective configuration and source fingerprints. Historical evidence is immutable; raw datasets remain read-only and derived outputs use separate paths.

### Output formats and compatibility

EDC identifies output formats with `edc_*_v1` and validates supported versions when reading.
Use a fresh output directory: old DQS catalogs,
records, dataset references, reviews and source mappings are rejected. No legacy-result
converter or fallback is provided. New EDC workflows support resume.
The `framework_version` field in `run.json` records the installed distribution version,
or `unknown` when no distribution is installed.

| Artifacts | Schema |
|---|---|
| Record, catalog, run | `edc_record_v1`, `edc_catalog_v1`, `edc_run_v1` |
| Episode recommendations, pool | `edc_recommendations_v1`, `edc_recommendation_pool_v1` |
| Dataset reference, review | `edc_dataset_ref_v1`, `edc_review_v1` |
| Media cache, channels, units | `edc_media_cache_v1`, `edc_channel_catalog_v1`, `edc_units_v1` |
| Numeric coverage, regeneration, source mapping | `edc_numeric_coverage_v1`, `edc_regenerate_manifest_v1`, `edc_source_mapping_v1` |

The sidecar is `edc-source-mapping.json`; reader profile metadata uses `edc_profile`.
Constants are maintained in `edc.schemas`. For custom value algorithms, see
[algorithm plugins](../how-to/ALGORITHM_PLUGIN.md) and
[algorithm integration](../how-to/ALGORITHM_INTEGRATION.md).

<a id="readers"></a>
## Reader interface

Reader plugins register under `edc.readers` and must:

- Implement `can_open(path)` and `open_dataset(path, ...)`, preserving Dataset → Episode → Channel.
- Preserve episode ID, source, and selector; never derive episode count from file count.
- Distinguish acquisition, container, playback, synthetic, and unknown time; preserve unknown units and semantics.
- Verify numeric, image, time, and provenance reads against declarations, including missing and failed inputs.

For steps and installation, see [Reader Plugins](../how-to/READER_PLUGIN.md).

## Related pages

- [Algorithm Plugins](../how-to/ALGORITHM_PLUGIN.md)
- [Reader Plugins](../how-to/READER_PLUGIN.md)
- [Framework Design](../explanation/ARCHITECTURE.md)
- [Built-in quality algorithm reference](ALGORITHMS.md)
