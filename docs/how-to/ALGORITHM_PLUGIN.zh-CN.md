# 算法扩展

[English](ALGORITHM_PLUGIN.md) · [接口参考](../reference/OUTPUTS.zh-CN.md)

质量、价值、编辑与增强算法均可通过 `edc.algorithms` 插件接口扩展。独立 Python 包安装后，由 EDC 自动发现，与内置算法共用数据读取、参数配置、执行和结果保存。

外部插件与内置算法的目录、接口和验证步骤见[算法接入与内化](ALGORITHM_INTEGRATION.zh-CN.md)。本页以质量算法演示报告接口，其他类别保留各自的结果含义。

本例实现关节跳变检查：读取关节反馈，计算相邻样本的最大角度变化，输出逐通道检查结果。

## 1. 创建算法包

```text
edc-joint-jump/
├── pyproject.toml
└── joint_jump/
    ├── __init__.py
    ├── algorithm.py
    └── method.py
```

`__init__.py` 保持为空。`method.py` 保存计算方法，`algorithm.py` 负责声明输入、读取数据和返回结果。完整样例见 [examples/plugins/algorithm](../../examples/plugins/algorithm/)。

## 2. 实现计算方法

`joint_jump/method.py`：

```python
import numpy as np


def measure_steps(positions):
    steps = np.abs(np.diff(positions, axis=0))
    maximum = float(steps.max()) if steps.size else 0.0
    return maximum
```

输入为 `N × D` 关节角数组，单位为弧度；输出为最大相邻变化量。已有方法可在此复用计算逻辑。

## 3. 实现算法接口

`joint_jump/algorithm.py`：

```python
from edc import AlgorithmSpec, Item, QualityAlgorithm
from .method import measure_steps


class JointJump(QualityAlgorithm):
    spec = AlgorithmSpec(
        id="acme__joint_jump",
        version="0.1.0",
        name="关节跳变",
        author="Acme Robotics",
        description="检查相邻样本的关节角变化。",
        presets=("quality_cpu",),
        parameter_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "review_rad": {"type": "number", "exclusiveMinimum": 0,
                               "default": 0.2},
            },
        },
        input_descriptions=("关节位置反馈，以弧度为单位，至少两个样本。",),
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
                         reason="缺少关节位置反馈。")]

        items = []
        for channel in channels:
            positions = episode.read_signal(channel.name, "position")
            if len(positions) < 2:
                items.append(Item(channel.name, status="not_applicable",
                                  reason="至少需要两个样本。"))
                continue
            maximum = measure_steps(positions)
            items.append(Item(
                channel.name,
                verdict="REVIEW" if maximum > threshold else "PASS",
                reason=f"最大相邻变化 {maximum:.3f} 弧度。",
                metrics={"max_step_rad": maximum},
            ))
        return items


plugin = JointJump()
```

| 接口 | 职责 |
|---|---|
| `AlgorithmSpec` | 声明 ID、版本、参数和输入要求 |
| `check(episode, params)` | 对一条轨迹执行检查 |
| `episode.channels` | 按模态、字段和反馈类型选择通道 |
| `episode.read_signal()` | 读取执行器准备的国际单位制数值数组 |
| `Item` | 保存检查对象、判断、指标和证据 |

外部算法 ID 使用 `team__algorithm`。`presets=("quality_cpu",)` 将算法加入批量质量集合；仅按需执行时可省略。示例阈值用于演示，实际阈值在机器人配置中设置。

需要在推荐阶段检查特定输入时，可通过 `AlgorithmSpec.input_requirements` 或 `data_requirements` 声明绑定条件，字段定义见[接口参考](../reference/OUTPUTS.zh-CN.md)。模型依赖在 `spec.dependencies` 中登记，并在运行函数内导入。

## 4. 注册并安装

`pyproject.toml`：

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

在 EDC 所在环境安装：

```bash
python -m pip install -e /path/to/edc-joint-jump
edc algorithms
```

算法列表出现 `acme__joint_jump` 即完成注册。编辑安装会直接使用算法包中的最新代码；分发时可将该包构建为 wheel。

## 5. 配置并运行

在机器人 YAML 的 `checks` 下设置参数：

```yaml
checks:
  acme__joint_jump:
    review_rad: 0.1
```

先单独运行新算法：

```bash
edc run /path/to/data --robot /path/to/robot.yaml \
  --algorithm acme__joint_jump --output /path/to/results/joint-jump
```

再与内置算法一起执行：

```bash
edc batch /path/to/data --robot /path/to/robot.yaml \
  --output /path/to/results/report.html --jobs 1
```

单次运行生成 `report.html` 和运行记录；批量报告增加“关节跳变”一列。完整指标与证据通过 `catalog.json` 定位。`batch` 首次使用或配置变更后，会要求确认并将签名写回机器人 YAML。

## 6. 验证算法

- 用固定数组验证计算结果和阈值边界。
- 用已配置轨迹验证通道选择、单位和参数生效。
- 用缺少反馈、样本不足的轨迹验证不适用结果。
- 检查报告中的判断、指标和证据与方法输出一致。

`Item` 支持 `PASS`、`REVIEW`、`SALVAGE`、`EXCLUDE`；未执行的检查使用 `not_applicable` 或 `failed`。报告摘要可通过 `AlgorithmResult(spec.id, items, note)` 提供。完整字段见[输出对象与接口参考](../reference/OUTPUTS.zh-CN.md)。

通用质量、价值、编辑与增强方法使用 `AlgorithmSpec + run(context, params)`；集合算法另实现 `preflight_dataset()`，见[通用插件接口](../reference/OUTPUTS.zh-CN.md#plugins)。
