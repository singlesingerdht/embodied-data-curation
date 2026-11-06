# Add a Quality Algorithm

[简体中文](ALGORITHM_PLUGIN.zh-CN.md) · [Interface reference](../reference/OUTPUTS.md)

Quality algorithms extend EDC through the `edc.algorithms` plugin interface. Installed Python packages are discovered automatically and share data loading, parameter configuration, execution, and reporting with built-in algorithms.

This example implements a joint-step check: read joint feedback, measure the largest change between adjacent samples, and return one result per channel.

## 1. Create a package

```text
edc-joint-jump/
├── pyproject.toml
└── joint_jump/
    ├── __init__.py
    ├── algorithm.py
    └── method.py
```

Leave `__init__.py` empty. Keep computation in `method.py`; use `algorithm.py` for metadata, data access, and results. A complete example is available in [examples/plugins/algorithm](../../examples/plugins/algorithm/).

## 2. Implement the method

`joint_jump/method.py`:

```python
import numpy as np


def measure_steps(positions):
    steps = np.abs(np.diff(positions, axis=0))
    maximum = float(steps.max()) if steps.size else 0.0
    return maximum
```

The input is an `N × D` joint-position array in radians. The output is the largest adjacent change. Existing computational methods can be reused here.

## 3. Implement the algorithm interface

`joint_jump/algorithm.py`:

```python
from edc import AlgorithmSpec, Item, QualityAlgorithm
from .method import measure_steps


class JointJump(QualityAlgorithm):
    spec = AlgorithmSpec(
        id="acme__joint_jump",
        version="0.1.0",
        name="Joint step",
        author="Acme Robotics",
        description="Check joint-position changes between adjacent samples.",
        presets=("quality_cpu",),
        parameter_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "review_rad": {"type": "number", "exclusiveMinimum": 0,
                               "default": 0.2},
            },
        },
        input_descriptions=("Joint-position feedback in rad, at least two samples.",),
    )

    def check(self, episode, params):
        threshold = params.get("review_rad", 0.2)
        channels = [
            c for c in episode.channels.values()
            if c.modality == "joint_space"
            and c.source_kind == "feedback"
            and "position" in c.fields
        ]
        if not channels:
            return [Item("joints", status="not_applicable",
                         reason="Joint-position feedback is missing.")]

        items = []
        for channel in channels:
            positions = episode.read_signal(channel.name, "position")
            if len(positions) < 2:
                items.append(Item(channel.name, status="not_applicable",
                                  reason="At least two samples are required."))
                continue
            maximum = measure_steps(positions)
            items.append(Item(
                channel.name,
                verdict="REVIEW" if maximum > threshold else "PASS",
                reason=f"Largest adjacent change: {maximum:.3f} rad.",
                metrics={"max_step_rad": maximum},
            ))
        return items


plugin = JointJump()
```

| Interface | Role |
|---|---|
| `AlgorithmSpec` | Algorithm ID, version, parameters, and input requirements |
| `check(episode, params)` | Check one episode |
| `episode.channels` | Select channels by modality, field, and feedback type |
| `episode.read_signal()` | Read SI numeric arrays prepared by the executor |
| `Item` | Store the target, verdict, metrics, and evidence |

External IDs use `team__algorithm`. `presets=("quality_cpu",)` adds the algorithm to the batch quality collection; omit it for explicit execution only. The example threshold is illustrative; configure application thresholds in the robot YAML.

For recommendation-time input checks, declare bindings with `AlgorithmSpec.input_requirements` or `data_requirements`; see the [interface reference](../reference/OUTPUTS.md). Declare model dependencies in `spec.dependencies` and import them inside the execution function.

## 4. Register and install

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "edc-joint-jump"
version = "0.1.0"
dependencies = ["embodied-data-curation>=0.3.0a1"]

[project.entry-points."edc.algorithms"]
acme__joint_jump = "joint_jump.algorithm:plugin"

[tool.setuptools]
packages = ["joint_jump"]
```

Install in the same environment as EDC:

```bash
python -m pip install -e /path/to/edc-joint-jump
edc algorithms
```

Registration is complete when `acme__joint_jump` appears in the list. Editable installs use the latest package source; build a wheel for distribution.

## 5. Configure and run

Add parameters under `checks` in the robot YAML:

```yaml
checks:
  acme__joint_jump:
    review_rad: 0.1
```

Run the new algorithm individually:

```bash
edc run /path/to/data --robot /path/to/robot.yaml \
  --algorithm acme__joint_jump --output /path/to/results/joint-jump
```

Then run it with the built-in collection:

```bash
edc batch /path/to/data --robot /path/to/robot.yaml \
  --output /path/to/results/report.html --jobs 1
```

Single runs produce `report.html` and run records; batch reports add a “Joint step” column. Locate full metrics and evidence through `catalog.json`. On first use or after configuration changes, `batch` requests confirmation and writes a signature to the robot YAML.

## 6. Validate

- Verify computation and threshold boundaries with fixed arrays.
- Verify channel selection, units, and parameters with a configured episode.
- Check inapplicable results for missing feedback and insufficient samples.
- Compare report verdicts, metrics, and evidence with the method output.

`Item` supports `PASS`, `REVIEW`, `SALVAGE`, and `EXCLUDE`; unexecuted checks use `not_applicable` or `failed`. Return `AlgorithmResult(spec.id, items, note)` to add a report summary. See the [output reference](../reference/OUTPUTS.md) for field definitions.

General quality, value, editing, and augmentation methods use `AlgorithmSpec + run(context, params)`. Dataset methods also implement `preflight_dataset()`; see [general plugin interfaces](../reference/OUTPUTS.md#plugins).
