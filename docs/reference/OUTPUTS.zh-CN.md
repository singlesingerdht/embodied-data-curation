# 接口与输出参考

[返回主页](../../README.zh-CN.md) · [English](OUTPUTS.md) · [算法扩展](../how-to/ALGORITHM_PLUGIN.zh-CN.md)

算法输入、质量结果、插件与运行记录的公共接口。
如何写一个算法插件见[算法扩展](../how-to/ALGORITHM_PLUGIN.zh-CN.md)；为什么这样划分见[框架设计](../explanation/ARCHITECTURE.zh-CN.md)。

<a id="interfaces"></a>
## 接口类型

| 接口 | 适用对象 | 开发者实现 |
|---|---|---|
| `QualityAlgorithm` | 按轨迹输出检查项的质量算法 | `spec` 与 `check(episode, params) -> list[Item]` |
| `AlgorithmSpec + run(context, params)` | 通用质量、价值、编辑和增强插件 | 元数据、输入／参数要求与 `run`；dataset 粒度另需 `preflight_dataset` |
| 读取器插件 | 新数据格式 | `can_open()` 和 `open_dataset()`，返回统一数据接口 |

公共类型从 `edc` 导入，包括 `AlgorithmSpec`、`QualityAlgorithm`、`Item` 和 `AlgorithmResult`。读取器、机器人配置、SI 执行视图及预检共同准备输入，单位与通道语义须明确声明。

<a id="context"></a>
## 算法输入视图

算法通过机器人 YAML 声明的通道名访问 `episode`：

| 成员 | 提供什么 |
|---|---|
| `channels` | 每路的类型、字段、时间轴、时钟 |
| `read_signal(通道, 字段)` | 执行器提供的 episode 中，已确认物理字段的 SI 数组 |
| `read_values` / `iter_frames` | 逐行、逐帧读取 |
| `profile` | 机器人 yaml 内容 |
| `raw(通道)` | 只读原始记录：原始整数时间、序列号、消息类型 |

<a id="outputs"></a>
## 质量输出对象

| 对象 | 职责 |
|---|---|
| `Item` | 一个检查对象：`target`、执行 `status`、质量 `verdict`、短 `reason`、`metrics`、`evidence`；可救时提供 `remedy` |
| `AlgorithmResult` | 一项算法的检查项列表及短摘要；`level` 自动按检查项汇总，`to_dict()`／`from_dict()` 序列化 |
| `QualityAlgorithm` | 调用 `check()`，验证非空 Item 列表，为每项保存报告 |

```python
from edc import Item, AlgorithmResult

items = [Item('左臂', verdict='REVIEW', reason='关节变化超过设定阈值。',
              metrics={'max_step_rad': 0.3}, evidence=[{'row': 12}])]
payload = AlgorithmResult('team_example__joint_jump', items).to_dict()
```

- `status='checked'` 必须给出 `PASS`／`REVIEW`／`SALVAGE`／`EXCLUDE` 之一。
- `status='not_applicable'` 或 `'failed'` 时不填 `verdict`，必须写原因；未执行不能返回通过。未捕获的运行异常由执行器记录为处理错误。
- 非通过项须有 `reason`；`SALVAGE` 还须有 `remedy`。原因用短句指出对象与问题，指标和源行放入各自字段。
- 不手工生成跨算法总分。统一运行记录使用 `edc_record_v1` schema。

<a id="plugins"></a>
## 通用插件与运行上下文

插件注册到 `edc.algorithms`。`AlgorithmSpec` 定义 ID、版本、类别、粒度、输入条件、参数 schema、依赖及设备；类别为 quality／value／editing／augmentation。外部 ID 使用 `team__algorithm`，重复 ID 或不兼容接口版本明确失败。模型与 GPU 依赖按需加载。

`run(context, params)` 读取当前 episode 或 dataset；工作流通过只读 `context.upstream` 提供上游记录。dataset 方法对当前集合运行一次，需在 `preflight_dataset()` 中核对集合条件。

质量便利基类不要求价值方法输出质量判断，也不把分布或实验估值换成一个分数。编辑／增强按现有约定返回 `output / params / source_mapping`，副本经验证后再质检；新数据不能覆盖源文件。当前变换验证器只接受行不增减、时间不变的 identity 映射；当前不支持时间重采样、任意片段修改或通道增删。每个编辑／增强输出分支都必须紧接质量阶段，否则配置检查失败。

[合成插件](../../examples/plugins/workflow/)提供受支持契约的示例。

## 运行产物

`catalog.json` 索引输出。每个 `_runs/<run_id>` 保存配置、版本、记录、错误与证据；工作流另存完整生效配置与来源指纹。历史证据不可改写，原始数据只读，派生输出使用独立路径。

### 输出格式与兼容范围

EDC 使用 `edc_*_v1` 标识输出格式，读取时校验受支持的版本。
不提供旧 DQS 结果兼容或转换。请为新运行指定新输出目录；旧 catalog、record、数据引用、
复核与旧来源映射明确拒绝，不能恢复旧运行。新 EDC 工作流可正常恢复。
`run.json` 的 `framework_version` 记录实际安装包版本；未安装发行包时为 `unknown`。

| 产物 | schema |
|---|---|
| 算法记录、目录、运行 | `edc_record_v1`、`edc_catalog_v1`、`edc_run_v1` |
| 单集推荐、推荐池 | `edc_recommendations_v1`、`edc_recommendation_pool_v1` |
| 数据引用、复核 | `edc_dataset_ref_v1`、`edc_review_v1` |
| 媒体缓存、通道目录、单位证据 | `edc_media_cache_v1`、`edc_channel_catalog_v1`、`edc_units_v1` |
| 数值覆盖、重新导出、来源映射 | `edc_numeric_coverage_v1`、`edc_regenerate_manifest_v1`、`edc_source_mapping_v1` |

来源映射文件名为 `edc-source-mapping.json`，读取器的配置元数据键为 `edc_profile`。
名称常量集中于 `edc.schemas`。
自定义价值算法的接入方式见[插件开发](../how-to/ALGORITHM_PLUGIN.zh-CN.md)
与[算法接入与内化](../how-to/ALGORITHM_INTEGRATION.zh-CN.md)。

<a id="readers"></a>
## 读取器接口

读取器插件注册到 `edc.readers`，须满足：

- 实现 `can_open(path)` 和 `open_dataset(path, ...)`，保留 Dataset → Episode → Channel。
- 明确 episode ID、源文件与 selector；不按文件数猜轨迹数。
- 区分采集、容器、播放、生成及未知时间；未知语义和单位保留未知。
- 图像、数值、时间读取及来源映射须与声明一致，验证可用／缺输入／失败情况。

编写步骤与安装方式见[读取器扩展](../how-to/READER_PLUGIN.zh-CN.md)。

## 相关页面

- [算法扩展](../how-to/ALGORITHM_PLUGIN.zh-CN.md)
- [读取器扩展](../how-to/READER_PLUGIN.zh-CN.md)
- [框架设计](../explanation/ARCHITECTURE.zh-CN.md)
- [内置质量算法参考](ALGORITHMS.zh-CN.md)
