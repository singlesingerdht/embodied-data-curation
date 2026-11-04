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

<a id="outputs"></a>
## Quality output objects

| Object | Responsibility |
|---|---|
| `Item` | One target: execution `status`, quality `verdict`, short `reason`, `metrics`, `evidence`, and a `remedy` for salvage |
| `AlgorithmResult` | Items and a short note for one method; computed `level`; `to_dict()` / `from_dict()` serialization |
| `QualityAlgorithm` | Calls `check()`, validates a nonempty Item list, and wraps `{'edc_result': ...}`; subclasses need no extra wrapper |

```python
from edc import Item, AlgorithmResult

items = [Item('left arm', verdict='REVIEW', reason='Joint step exceeds the declared threshold.',
              metrics={'max_step_rad': 0.3}, evidence=[{'row': 12}])]
payload = AlgorithmResult('team_example__joint_step', items).to_dict()
```

- `status='checked'` requires `PASS`, `REVIEW`, `SALVAGE`, or `EXCLUDE`.
- `status='not_applicable'` or `'failed'` requires a reason and no verdict. Unchecked items cannot pass. The executor records uncaught exceptions as processing errors.
- Every non-pass item needs a reason; SALVAGE also needs a remedy. Keep reasons short and put measurements and source rows in their respective fields.
- Do not create cross-algorithm scores. Run records retain the existing `dqs_record_v1` schema identifier; it is not another public command namespace.

<a id="quality-example"></a>
## Complete quality example

This example demonstrates **SI input → quality items → registration → execution**, reading left-arm joint feedback. Adjacent position change is not velocity; `0.2 rad` is an educational parameter, not a universal safety threshold. This separately installed plugin is not one of the nine built-ins.

Source: [`joint_step.py`](../examples/plugins/quality_base/joint_step.py). The code below matches the file:

```python
"""Runnable output-contract example; not a calibrated robot safety method."""
import numpy as np
from edc import AlgorithmSpec, Item, QualityAlgorithm


class JointStep(QualityAlgorithm):
    spec = AlgorithmSpec(
        id='team_example__joint_step', version='0.1.0',
        name='Joint step example', author='Example team',
        description='Adjacent left-arm feedback changes; demonstrates the quality API.',
        presets=('quality_cpu',), example=True,
        input_requirements=({'channel': 'joint/arm/left/feedback',
                             'field': 'position', 'unit': 'rad'},),
        parameter_schema={'type': 'object', 'additionalProperties': False,
                          'properties': {'review_rad': {'type': 'number',
                                         'exclusiveMinimum': 0, 'default': 0.2}}})

    def check(self, episode, params):
        name = 'joint/arm/left/feedback'
        # The executor provides the SI view and resolves the declared logical name.
        angles = np.asarray(episode.read_signal(name, 'position'), dtype=float)
        if len(angles) < 2:
            return [Item(name, status='not_applicable', reason='At least two rows are required.')]
        if not np.isfinite(angles).all():
            return [Item(name, verdict='EXCLUDE', reason='Nonfinite joint positions.')]
        step = np.abs(np.diff(angles, axis=0)).reshape(len(angles) - 1, -1).max(axis=1)
        threshold = params.get('review_rad', 0.2)
        rows = np.flatnonzero(step > threshold) + 1
        return [Item(name, verdict='REVIEW' if rows.size else 'PASS',
                     reason=f'{len(rows)} adjacent steps exceed {threshold:g} rad.',
                     metrics={'max_step_rad': float(step.max()), 'threshold_rad': threshold},
                     evidence=[{'row': int(row)} for row in rows[:20]])]


plugin = JointStep()
```

`input_requirements` declares the logical channel, field, and target SI unit. Preflight and execution use the same binding; missing input/units block physical computation. This example intentionally checks the left arm. A production plugin with configurable arms must declare and implement its own scope parameters.

### Register and run

See the complete [`pyproject.toml`](../examples/plugins/quality_base/pyproject.toml). Its entry point is:

```toml
[project.entry-points."edc.algorithms"]
team_example__joint_step = "joint_step:plugin"
```

From the repository directory, in an isolated environment with EDC installed:

```bash
pip install ./examples/plugins/quality_base
edc run /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --algorithm team_example__joint_step \
  --output /path/to/results/plugin
```

Use `catalog.json` to locate run records and `quality.json`, including the plugin's items, metrics, and source rows. `edc batch` also includes plugins registered in `quality_cpu`, adding an algorithm column. Remove this example independently with `pip uninstall edc-quality-base-example`.

The existing [multi-joint iteration example](../examples/plugins/algorithm/joint_jump.py) remains available for its stated joint inputs. Production plugins should declare minimum inputs, semantics, units, parameter schemas, and dependencies; prose alone is not executable preflight.

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
