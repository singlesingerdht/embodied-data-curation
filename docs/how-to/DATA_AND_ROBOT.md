# Data and Robot Configuration

[简体中文](DATA_AND_ROBOT.zh-CN.md)

Datasets define episode boundaries. Robot YAML defines channel semantics, units, and parts. This workflow covers source inspection, configuration, validation, and quality checks.

## 1. Prepare the data

Run the inspection script from the repository root to list actual topics, columns, fields, and dimensions:

```bash
python examples/quickstart/list_inputs.py /path/to/data
```

Preserve LeRobot metadata and directory structure. Use episode groups for HDF5. For an explicit collection, place `dataset.yaml` in the dataset directory:

```yaml
schema_version: 1
id: task_dataset
version: '1'
episodes:
  - id: take_001
    path: take_001.mcap
    format: mcap
    selector: {}
```

Place `take_001.mcap` in the same directory and pass the directory path when opening the dataset. Specify `format` explicitly. Use reader-supported selectors, such as `{group: data/demo_0}` for HDF5. Each episode needs a unique ID.

<a id="dataset"></a>
<a id="robot"></a>
## 2. Create robot YAML

Copy a [single-arm](../../configs/templates/single_arm.yaml), [dual-arm](../../configs/templates/dual_arm.yaml), or [dual-arm with hands](../../configs/templates/dual_arm_hand.yaml) template. The following example describes six rotational joints in tabular data without recorded acquisition timestamps:

```yaml
robot:
  name: lab_arm
  confirmed_by: data_owner
data:
  time_note: No original acquisition timestamps were recorded
arms:
  main:
    joints: {source: observation.state, index: 0-5, unit: rad}
checks:
  skip: [Reset_Detection]
```

When original acquisition timestamps are available, replace `time_note` with the actual column and unit:

```yaml
data:
  time: {source: capture_timestamp, unit: ns}
```

### Map fields

| Setting | Meaning | Example |
|---|---|---|
| `source` | Source topic or column | `observation.state`, `/joint_states` |
| `field` | Message member | `position` |
| `index` | Array components, inclusive ranges | `0-5`, `[0, 2, 4]` |
| `unit` | Source unit | `rad`, `deg`, `m` |

For ROS joint messages:

```yaml
arms:
  main:
    joints: {source: /joint_states, field: position, index: 0-5, unit: rad}
```

Use `main` for one arm and `left`/`right` for two arms. Split combined arrays with distinct indices. Add end-effector, camera, gripper, and IMU entries for recorded signals. Set algorithm parameters under `checks.<algorithm_id>`. `Reset_Detection` requires a confirmed Home reference and is skipped here. See the [configuration reference](../reference/DATA_AND_ROBOTS.md) for fields and units.

## 3. Validate the mapping

```bash
edc robot check --robot /path/to/robot.yaml --data /path/to/data
```

This command reads declared channels and checks numeric ranges in the first episode. Confirm source fields, component order, and units before running quality checks.

<a id="urdf"></a>
### URDF

If you have a URDF, the command can generate a YAML fragment for its joint chain. First inspect the link tree and choose the arm's base and tip:

```bash
edc robot urdf --urdf /path/to/robot.urdf
```

Specify the chain to print a copyable `urdf` configuration:

```bash
edc robot urdf --urdf /path/to/robot.urdf --base base_link --tip tool_link
```

Merge the output under `arms.main` or `arms.left/right`, alongside `joints`:

```yaml
urdf:
  file: robot/arm.urdf
  joints: [joint1, joint2, joint3, joint4, joint5, joint6]
  base_link: base_link
  tip_link: tool_link
```

EDC reads joint limits, velocity limits, and kinematics from the URDF. The command prints the joint-chain fragment; complete the YAML with data fields, units, time, and cameras. Order `urdf.joints` to match the recorded columns. `urdf.file` resolves relative to the robot YAML. With two episodes and measured end-effector positions, validate the chain and frame relation:

```bash
edc robot urdf --robot /path/to/robot.yaml \
  --data /path/to/episode_1 /path/to/episode_2
```

## 4. Run quality checks

```bash
python examples/quickstart/recommend.py /path/to/data --robot /path/to/robot.yaml
edc batch /path/to/data --robot /path/to/robot.yaml \
  --output /path/to/results/report.html --jobs 1
```

Recommendations list available algorithms and input requirements. `batch` produces HTML and JSON reports; first use or configuration changes require confirmation and write a signature to the YAML. Keep results outside source data.

### Python API

Compile the card and pass the same parameters to recommendation and execution:

```python
from pathlib import Path
import yaml
import edc

robot = Path("/path/to/robot.yaml")
card = yaml.safe_load(robot.read_text(encoding="utf-8"))
profile, params, _ = edc.compile_card(card, base=robot.parent)

with edc.open_dataset("/path/to/data", profile=profile) as dataset:
    recommendations = edc.recommend(dataset, params=params)
    result = edc.run(dataset, output="/path/to/results/run", params=params)
```

[Run Algorithms](RUN_AND_SELECT.md) · [Formats and configuration](../reference/DATA_AND_ROBOTS.md)
