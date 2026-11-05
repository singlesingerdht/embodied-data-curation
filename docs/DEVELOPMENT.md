# Algorithm Development and Output Contract

[Home](../README.md) · [简体中文](DEVELOPMENT.zh-CN.md) · [Contributing](../CONTRIBUTING.md)

<a id="interfaces"></a>
## Choose an interface

| Interface | Use | Implement |
|---|---|---|
| `QualityAlgorithm` | Per-episode quality items | `spec` and `check(episode, params) -> list[Item]` |
| `AlgorithmSpec + run(context, params)` | General quality, value, editing, and augmentation plugins | Metadata, input/parameter requirements, and `run`; dataset methods also need `preflight_dataset` |
| Reader plugin | A new data format | `can_open()` and `open_dataset()` returning the common data model |

Public types are exported by `edc`. Readers, robot semantics, SI execution views, and preflight prepare inputs together. There is no need for an all-purpose InputBase; algorithms must not guess units or semantics.

The `episode` an algorithm receives is addressed by robot-yaml channel names: `channels` (type, fields, timeline, clock per channel), `read_signal(channel, field)` (SI arrays), `read_values` and `iter_frames` (rows, frames), `profile` (the yaml), and `raw(channel)` (read-only source record: raw integer times, sequence numbers, message type).

Each of the nine built-in methods owns one folder, `src/edc/algorithms/quality/algorithms/<method>/`, holding all code specific to it in three fixed files: `algorithm.py` with `Algorithm`, a `BuiltinAlgorithm` (itself a `QualityAlgorithm`) declaring name, inputs, parameters, and pre-run checks, whose `assess()` extracts the method inputs; `method.py`, the method itself (computation only; other helper files are imported from it); and `result.py`, which turns the method report into Items (optional hooks are listed at the top of `shared/verdicts.py`). Built-in and plugin Items are both stored with `package`. Shared code names no method; report order is defined only by `ORDER` in `adapters/cpu.py`.

<a id="outputs"></a>
## Quality output objects

| Object | Responsibility |
|---|---|
| `Item` | One target: execution `status`, quality `verdict`, short `reason`, `metrics`, `evidence`, and a `remedy` for salvage |
| `AlgorithmResult` | Items and a short note for one method; computed `level`; `to_dict()` / `from_dict()` serialization |
| `QualityAlgorithm` | Calls `check()`, validates a nonempty Item list, and stores one report per Item with `package` (built-ins use it too); subclasses need no extra wrapper |

```python
from edc import Item, AlgorithmResult

items = [Item('left arm', verdict='REVIEW', reason='Joint step exceeds the declared threshold.',
              metrics={'max_step_rad': 0.3}, evidence=[{'row': 12}])]
payload = AlgorithmResult('team_example__joint_jump', items).to_dict()
```

- `status='checked'` requires `PASS`, `REVIEW`, `SALVAGE`, or `EXCLUDE`.
- `status='not_applicable'` or `'failed'` requires a reason and no verdict. Unchecked items cannot pass. The executor records uncaught exceptions as processing errors.
- Every non-pass item needs a reason; SALVAGE also needs a remedy. Keep reasons short and put measurements and source rows in their respective fields.
- Do not create cross-algorithm scores. Run records retain the existing `dqs_record_v1` schema identifier; it is not another public command namespace.

<a id="quality-example"></a>
## Adding a new algorithm

There are two ways in; the folder looks the same (`algorithm.py` connects to the framework, `method.py` only computes), and only the output differs:

| Case | Files | Output |
|---|---|---|
| A newly written method (preferred) | `algorithm.py` + `method.py` + `README.md` | `check()` returns `Item`s directly |
| An existing method with its own report format | plus `result.py` | `method.py` returns its report, `result.py` turns it into `Item`s (as the nine built-ins do) |

Start as a plugin (a separate package; installing it is enough, this repository is unchanged) and promote it to a built-in once accepted. Template: [`examples/plugins/algorithm`](../examples/plugins/algorithm/) (joint jump; demonstration thresholds only).

### Write

- `spec` (`AlgorithmSpec`): id `team__algorithm`; the third field is the report column name; `presets=('quality_cpu',)` runs it in `edc batch`; parameters and defaults in `parameter_schema`, overridden under `checks.<id>` in the robot yaml.
- `check(episode, params)`: find channels by type (for example joint feedback position) rather than fixed names; read with `read_signal` (SI units); leave the judgment to `method.py`; return a list of `Item`. To give the report cell a one-line summary, return `AlgorithmResult(spec.id, items, note)`. Missing data gives a `not_applicable` item with a reason.
- `method.py`: computation only, arrays in and numbers/verdicts out, testable on its own.

### Register and run

One entry point in the plugin's `pyproject.toml` lets `edc` find it:

```toml
[project.entry-points."edc.algorithms"]
team_example__joint_jump = "joint_jump.algorithm:plugin"
```

```bash
pip install -e examples/plugins/algorithm      # install
edc batch DATA --robot ROBOT                   # one more report column, part of the episode decision
pip uninstall edc-example-algorithm            # remove
```

A plugin installed in a shared environment runs in everyone's `edc batch`; try plugins in a separate environment.

### Promote

Rename the folder into `src/edc/algorithms/quality/algorithms/` (for example `Joint_Jump/`), change the `spec` id to the same name, add it to `ORDER` in `quality/adapters/cpu.py`, and drop the plugin entry point. With the class named `Algorithm` and a relative import of method, the code is unchanged.

## General plugins and context

`AlgorithmSpec` declares ID, version, category, granularity, input conditions, parameter schemas, dependencies, and device. External IDs use `team__algorithm`; duplicate IDs and incompatible interfaces fail explicitly. Load model/GPU dependencies lazily.

`run(context, params)` reads the current episode or dataset. Workflows expose upstream records through read-only `context.upstream`. A dataset method runs once on the current collection and checks collection requirements in `preflight_dataset()`.

The quality helper does not force value methods to return quality judgments or collapse distributions and experimental estimates into a score. Editing/augmentation returns `output / params / source_mapping`; validate derived copies before quality rechecks. Existing [workflow documentation, Chinese](WORKFLOWS.md) and [synthetic plugins](../examples/plugins/workflow/) explain interface boundaries, not validated integration and validation of specific algorithms.

<a id="readers"></a>
## Reader plugins

Use [`manifest_reader.py`](../examples/plugins/reader/manifest_reader.py) and its [`pyproject.toml`](../examples/plugins/reader/pyproject.toml) as a template, registering under `edc.readers`.

- Implement `can_open(path)` and `open_dataset(path, ...)`, preserving Dataset → Episode → Channel.
- Preserve episode ID, source, and selector; never derive episode count from file count.
- Distinguish acquisition, container, playback, synthetic, and unknown time; preserve unknown units and semantics.
- Verify numeric, image, time, and provenance reads against declarations, including missing and failed inputs.

Install with `pip install ./examples/plugins/reader`. This example reads a custom manifest; it is not a complete robot driver.

## Validation

Check available/missing inputs, unknown units, execution failures, provenance, and installed plugin discovery/execution. See [contributing](../CONTRIBUTING.md) for relevant tests and packaging checks.
