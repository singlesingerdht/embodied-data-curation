# Quality Algorithm Plugin Example

[简体中文](README.md) · [Development guide](../../../docs/how-to/ALGORITHM_PLUGIN.md)

`joint_jump` checks adjacent joint-position changes and returns per-channel metrics, evidence, and verdicts. Example thresholds are 0.2 rad for review and 0.5 rad for exclusion.

## Install and run

Run from the repository root:

```bash
python -m pip install -e ./examples/plugins/algorithm
edc algorithms
edc run /path/to/data --robot /path/to/robot.yaml \
  --algorithm team_example__joint_jump --output /path/to/results/joint-jump
```

The package registers through the `edc.algorithms` entry point in `pyproject.toml`. Installation also adds the algorithm to the `edc batch` quality collection.

| File | Role |
|---|---|
| `joint_jump/algorithm.py` | Metadata, channel reads, and results |
| `joint_jump/method.py` | Joint-step computation |
| `pyproject.toml` | Dependencies and registration |

Configure thresholds in robot YAML:

```yaml
checks:
  team_example__joint_jump:
    review_rad: 0.1
    exclude_rad: 0.5
```

See [Add a quality algorithm](../../../docs/how-to/ALGORITHM_PLUGIN.md) for package structure, interfaces, and the complete development workflow.
