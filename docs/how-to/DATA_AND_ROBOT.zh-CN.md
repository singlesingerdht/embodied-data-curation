# 数据与机器人配置

[English](DATA_AND_ROBOT.md)

数据集定义轨迹边界，机器人 YAML 定义通道语义、单位和部件。本流程从源数据开始，完成配置、核对和质量检查。

## 1. 准备数据

在仓库根目录运行字段查看脚本，记录实际的话题、列名、字段和维度：

```bash
python examples/quickstart/list_inputs.py /path/to/data
```

LeRobot 保留原始元数据 和目录结构；HDF5 使用轨迹组；自定义集合在数据集目录内保存 `dataset.yaml`：

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

将 `take_001.mcap` 放在该目录内，打开数据集时传目录路径。`format` 显式填写；`selector` 使用对应读取器支持的选项，例如 HDF5 的 `{group: data/demo_0}`。每条轨迹使用唯一 ID。

<a id="dataset"></a>
<a id="robot"></a>
## 2. 创建机器人 YAML

从 [单臂](../../configs/templates/single_arm.yaml)、[双臂](../../configs/templates/dual_arm.yaml)或[双臂与灵巧手](../../configs/templates/dual_arm_hand.yaml)模板复制配置。以下为六个转动关节、未记录采集时间的表格数据示例：

```yaml
robot:
  name: lab_arm
  confirmed_by: data_owner
data:
  time_note: 未记录原始采集时间戳
arms:
  main:
    joints: {source: observation.state, index: 0-5, unit: rad}
checks:
  skip: [Reset_Detection]
```

存在原始采集时间列时，将 `time_note` 替换为实际列与单位：

```yaml
data:
  time: {source: capture_timestamp, unit: ns}
```

### 映射字段

| 配置 | 含义 | 示例 |
|---|---|---|
| `source` | 源话题或列名 | `observation.state`、`/joint_states` |
| `field` | 消息内字段 | `position` |
| `index` | 数组分量，范围包含两端 | `0-5`、`[0, 2, 4]` |
| `unit` | 源数值单位 | `rad`、`deg`、`m` |

ROS 关节消息可写为：

```yaml
arms:
  main:
    joints: {source: /joint_states, field: position, index: 0-5, unit: rad}
```

单臂使用 `main`，双臂使用 `left` 和 `right`；合存数组通过不同 `index` 拆分。末端、相机、夹爪和 IMU 按实际记录添加。`checks.<algorithm_id>` 设置算法参数；`Reset_Detection` 需要已确认的归位参考位置，本例将其跳过。字段与单位见[配置参考](../reference/DATA_AND_ROBOTS.zh-CN.md)。

## 3. 核对配置

```bash
edc robot check --robot /path/to/robot.yaml --data /path/to/data
```

该命令核对机器人卡片声明的通道及数值范围，读取数据集的第一条轨迹。逐项确认源字段、分量顺序和单位后，再运行质量检查。

<a id="urdf"></a>
### URDF

已有 URDF 时，可由命令生成关节链的 YAML 片段。先查看连杆树，选择机械臂起点和末端：

```bash
edc robot urdf --urdf /path/to/robot.urdf
```

指定链后，命令会在终端输出可复制的 `urdf` 配置：

```bash
edc robot urdf --urdf /path/to/robot.urdf --base base_link --tip tool_link
```

将输出合并到机器人 YAML 的 `arms.main` 或 `arms.left/right` 下，与 `joints` 同级：

```yaml
urdf:
  file: robot/arm.urdf
  joints: [joint1, joint2, joint3, joint4, joint5, joint6]
  base_link: base_link
  tip_link: tool_link
```

框架从 URDF 读取关节限位、速度上限和运动学关系。命令只输出关节链片段；完整 YAML 仍需补齐数据字段、单位、时间和相机配置。`urdf.joints` 按实际数据列顺序排列，`urdf.file` 相对机器人 YAML 解析。有两条轨迹及实测末端时，可进一步核对关节链与坐标关系：

```bash
edc robot urdf --robot /path/to/robot.yaml \
  --data /path/to/episode_1 /path/to/episode_2
```

## 4. 运行质量检查

```bash
python examples/quickstart/recommend.py /path/to/data --robot /path/to/robot.yaml
edc batch /path/to/data --robot /path/to/robot.yaml \
  --output /path/to/results/report.html --jobs 1
```

推荐列出可运行算法及所需输入。`batch` 生成 HTML 与 JSON 报告，首次运行或配置变化后要求确认并写回 YAML 签名。输出目录与源数据分开。

### Python 接口

Python 调用中，先编译卡片，再将同一份参数传给推荐与执行：

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

[算法运行](RUN_AND_SELECT.zh-CN.md) · [数据格式与配置参考](../reference/DATA_AND_ROBOTS.zh-CN.md)
