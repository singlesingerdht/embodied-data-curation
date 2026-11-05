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

算法拿到的一条数据（`episode`）按机器人 yaml 的通道名取数：`channels`（每路的类型、字段、时间轴、时钟），`read_signal(通道, 字段)`（国际单位数组），`read_values`、`iter_frames`（逐行、逐帧），`profile`（yaml 内容），`raw(通道)`（原始记录：原始整数时间、序列号、消息类型，只读）。

九项内置算法各占 `src/edc/algorithms/quality/algorithms/<算法>/` 一个文件夹，只属于它的代码都在里面，固定三个文件：`algorithm.py` 里的 `Algorithm`（继承 `BuiltinAlgorithm`，它本身也是 `QualityAlgorithm`；写中文名、输入、参数、运行前检查，`assess()` 从数据里取出方法要的输入）；`method.py`（方法本体，只做计算，其余辅助文件由它引用）；`result.py`（把方法的报告翻成检查项，可选钩子见 `shared/verdicts.py` 开头）。内置和插件的检查项都经 `package` 存进运行记录。公共代码不点名任何算法，报告顺序只在 `adapters/cpu.py` 的 `ORDER` 定义。

<a id="outputs"></a>
## 质量输出对象

| 对象 | 职责 |
|---|---|
| `Item` | 一个检查对象：`target`、执行 `status`、质量 `verdict`、短 `reason`、`metrics`、`evidence`；可救时提供 `remedy` |
| `AlgorithmResult` | 一项算法的检查项列表及短摘要；`level` 自动按检查项汇总，`to_dict()`／`from_dict()` 序列化 |
| `QualityAlgorithm` | 调用 `check()`，验证非空 Item 列表，用 `package` 存成每项一份报告（内置算法也用它）；子类无需再写一套输出封装 |

```python
from edc import Item, AlgorithmResult

items = [Item('left arm', verdict='REVIEW', reason='Joint step exceeds the declared threshold.',
              metrics={'max_step_rad': 0.3}, evidence=[{'row': 12}])]
payload = AlgorithmResult('team_example__joint_jump', items).to_dict()
```

- `status='checked'` 必须给出 `PASS`／`REVIEW`／`SALVAGE`／`EXCLUDE` 之一。
- `status='not_applicable'` 或 `'failed'` 时不填 `verdict`，必须写原因；未执行不能返回通过。未捕获的运行异常由执行器记录为处理错误。
- 非通过项须有 `reason`；`SALVAGE` 还须有 `remedy`。原因用短句指出对象与问题，指标和源行放入各自字段。
- 不手工生成跨算法总分。统一运行记录继续使用现有 `dqs_record_v1` schema 标识；这不是另一个面向用户的入口。

<a id="quality-example"></a>
## 接入新算法

新算法有两种接法，文件夹样子相同（`algorithm.py` 接框架，`method.py` 只做计算），区别只在输出：

| 情况 | 文件 | 输出 |
|---|---|---|
| 新写的算法（推荐） | `algorithm.py` + `method.py` + `README.md` | `check()` 直接返回 `Item` |
| 别人给的旧格式现成方法 | 再加 `result.py` | `method.py` 出原报告，`result.py` 翻成 `Item`（同九个内置算法） |

先做成插件（单独的包，装上即用，不改本项目）；认可后再转正为内置算法。样板：[`examples/plugins/algorithm`](../examples/plugins/algorithm/)（关节跳变，阈值只作演示）。

### 写

- `spec`（`AlgorithmSpec`）：id 用 `团队__算法`，第三项是报告表头的中文名；`presets=('quality_cpu',)` 让 `edc batch` 一起跑；参数写在 `parameter_schema`（默认值），用户在机器人 yaml 的 `checks.<id>` 下改。
- `check(episode, params)`：按类型找通道（如关节反馈的 position），不写死通道名；读数据用 `read_signal`（已是国际单位）；判断交给 `method.py`；返回 `Item` 列表。想在报告格子写一句总结，返回 `AlgorithmResult(spec.id, items, note)`。缺数据时返回 `not_applicable` 项并写原因。
- `method.py`：纯计算，输入数组、输出数字和结论，可单独测试。

### 登记、运行

插件的 `pyproject.toml` 登记一行，`edc` 启动时按它找到算法：

```toml
[project.entry-points."edc.algorithms"]
team_example__joint_jump = "joint_jump.algorithm:plugin"
```

```bash
pip install -e examples/plugins/algorithm      # 装上
edc batch 数据目录 --robot 机型                  # 报告多一列“关节跳变”，参与整条结论
pip uninstall edc-example-algorithm            # 卸掉
```

共用环境里装上的插件会进每个人的 `edc batch`；试用请装在单独环境。

### 转正

文件夹改名搬进 `src/edc/algorithms/quality/algorithms/`（如 `Joint_Jump/`），`spec` 的 id 改成同名，在 `quality/adapters/cpu.py` 的 `ORDER` 里加上，删掉插件登记。类名用 `Algorithm`、导入 method 用相对路径，代码不用改。

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
