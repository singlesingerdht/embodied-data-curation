# 算法开发与输出规范

[返回主页](../README.zh-CN.md) · [English](DEVELOPMENT.md) · [贡献指南](../CONTRIBUTING.md)

<a id="interfaces"></a>
## 选择接入接口

| 接口 | 适用对象 | 开发者实现 |
|---|---|---|
| `QualityAlgorithm` | 按轨迹输出检查项的质量算法 | `spec` 与 `check(episode, params) -> list[Item]` |
| `AlgorithmSpec + run(context, params)` | 通用质量、价值、编辑和增强插件 | 元数据、输入／参数要求与 `run`；dataset 粒度另需 `preflight_dataset` |
| 读取器插件 | 新数据格式 | `can_open()` 和 `open_dataset()`，返回统一数据接口 |

公共类型由 `edc` 导出。输入由读取器、机器人语义、SI 执行视图及算法预检共同准备，不需要另建包办所有数据的 InputBase。算法内部不猜单位或通道含义。

<a id="outputs"></a>
## 质量输出对象

| 对象 | 职责 |
|---|---|
| `Item` | 一个检查对象：`target`、执行 `status`、质量 `verdict`、短 `reason`、`metrics`、`evidence`；可救时提供 `remedy` |
| `AlgorithmResult` | 一项算法的检查项列表及短摘要；`level` 自动按检查项汇总，`to_dict()`／`from_dict()` 序列化 |
| `QualityAlgorithm` | 调用 `check()`，验证非空 Item 列表，包装成 `{'edc_result': ...}`；子类无需再写一套输出封装 |

```python
from edc import Item, AlgorithmResult

items = [Item('left arm', verdict='REVIEW', reason='Joint step exceeds the declared threshold.',
              metrics={'max_step_rad': 0.3}, evidence=[{'row': 12}])]
payload = AlgorithmResult('team_example__joint_step', items).to_dict()
```

- `status='checked'` 必须给出 `PASS`／`REVIEW`／`SALVAGE`／`EXCLUDE` 之一。
- `status='not_applicable'` 或 `'failed'` 时不填 `verdict`，必须写原因；未执行不能返回通过。未捕获的运行异常由执行器记录为处理错误。
- 非通过项须有 `reason`；`SALVAGE` 还须有 `remedy`。原因用短句指出对象与问题，指标和源行放入各自字段。
- 不手工生成跨算法总分。统一运行记录继续使用现有 `dqs_record_v1` schema 标识；这不是另一个面向用户的入口。

<a id="quality-example"></a>
## 完整质量算法示例

本例固定读取左臂关节反馈，示范 **SI 输入 → 检查项 → 注册 → 执行**。相邻位置差不是速度；`0.2 rad` 仅是教学参数，不是通用安全阈值。本插件独立安装，不属于九项内置方法。

源码：[`joint_step.py`](../examples/plugins/quality_base/joint_step.py)。下列代码与该文件一致：

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

`input_requirements` 使用已确认的逻辑名、字段与目标 SI 单位。执行器将同一绑定用于预检和读取；缺输入／单位时不进入物理计算。此例明确检查左臂；需要支持可选臂的生产插件，应声明并实现自己的范围参数，不能依靠模糊名称自动猜测。

### 注册与运行

完整包定义见 [`pyproject.toml`](../examples/plugins/quality_base/pyproject.toml)，关键注册项：

```toml
[project.entry-points."edc.algorithms"]
team_example__joint_step = "joint_step:plugin"
```

在已安装 EDC 的独立环境中，从仓库目录执行：

```bash
pip install ./examples/plugins/quality_base
edc run /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --algorithm team_example__joint_step \
  --output /path/to/results/plugin
```

从输出目录的 `catalog.json` 找到运行记录和 `quality.json`，检查本插件的 `items`、指标与源行。用 `edc batch` 时，注册到 `quality_cpu` 的插件也会参与，并增加一个算法列。示例插件检查完可单独卸载：`pip uninstall edc-quality-base-example`。

已有[多关节遍历示例](../examples/plugins/algorithm/joint_jump.py)也保留，适用于其声明的关节输入；新插件应逐项声明最低输入、语义、单位、参数 schema 与依赖，避免仅靠描述文字替代预检。

## 通用插件与运行上下文

`AlgorithmSpec` 定义 ID、版本、类别、粒度、输入条件、参数 schema、依赖及设备；外部 ID 使用 `team__algorithm`，重复 ID 或不兼容接口版本明确失败。模型与 GPU 依赖按需加载。

`run(context, params)` 读取当前 episode 或 dataset；工作流通过只读 `context.upstream` 提供上游记录。dataset 方法对当前集合运行一次，需在 `preflight_dataset()` 中核对集合条件。

质量便利基类不要求价值方法输出质量判断，也不把分布或实验估值换成一个分数。编辑／增强按现有约定返回 `output / params / source_mapping`，副本经验证后再质检。现有[工作流文档](WORKFLOWS.md)与[合成插件](../examples/plugins/workflow/)说明接口边界；尚未据此宣称具体算法已接入并验证。

<a id="readers"></a>
## 读取器插件

参照 [`manifest_reader.py`](../examples/plugins/reader/manifest_reader.py) 与其 [`pyproject.toml`](../examples/plugins/reader/pyproject.toml)，注册到 `edc.readers`。

- 实现 `can_open(path)` 和 `open_dataset(path, ...)`，保留 Dataset → Episode → Channel。
- 明确 episode ID、源文件与 selector；不按文件数猜轨迹数。
- 区分采集、容器、播放、生成及未知时间；未知语义和单位保留未知。
- 图像、数值、时间读取及来源映射须与声明一致，验证可用／缺输入／失败情况。

安装示例：`pip install ./examples/plugins/reader`。它读取自定义清单，不是完整的新机器人驱动。

## 开发验证

先验证输入可用、输入缺失、单位未知、运行失败和来源完整性，再核对安装后可发现与执行。相关测试及安装要求见[贡献指南](../CONTRIBUTING.md)。
