# 算法与读取器接入

算法开发者声明 `AlgorithmSpec`，实现 `run(context, params)`；`context.episode` 和 `context.dataset` 提供执行 SI 视图，`context.workdir` 保存证据，`context.upstream` 是只读的上游结果。插件导入不加载模型，缺依赖、缺输入和执行失败分别报告。

```python
from edc import AlgorithmSpec

class Plugin:
    spec = AlgorithmSpec(
        'my_team__position', '1', '末端示例', 'My team', 'Input contract example',
        category='quality',
        parameter_schema={'type': 'object', 'properties': {'channel': {'type': 'string'}},
                          'required': ['channel'], 'additionalProperties': False},
        data_requirements=({'id': 'position', 'channel_param': 'channel',
                           'modality': 'pose', 'field': 'position', 'unit': 'm',
                           'source_kind': 'feedback'},))

    def run(self, context, params):
        values = context.episode.read_signal(params['channel'], 'position')
        return {'verdict': 'OBSERVATION', 'score': None, 'samples': len(values)}

plugin = Plugin()
```

这是观察性接口示例，不给质量通过结论。参数逻辑名在预检与执行中解析到同一份实际源绑定；不允许右侧请求回退左侧，也不以 command 代替 feedback。物理方法读取 SI；原值有限性专用接口是 `inspect_recorded_signal()`。

```toml
[project.entry-points."edc.algorithms"]
my_team__position = "my_plugin:plugin"
```

SDK 示例声明 `example=True`，会在自动生产方法选择中排除；可显式选择验证接口。每种输入、粒度、依赖与边界分别验收，不能以插件安装成功代表算法效果。

## 按臂与多输入

CPU 适配器的 `instances(episode, params)` 准备按臂实例，推荐与执行消费同一结果。第三方插件可实现该钩子并复用 `edc.robots.scope.instances()`：返回实例 ID、参数、input_scope、complete 和可选 error。不要在执行时重新按 topic 猜侧别；部件声明、指令／反馈和 SI 条件仍需检查。无此钩子的插件使用自己明确声明的通道输入，不自动加入不支持的 arms 参数。

## 数据集粒度与方法子集

`granularity='dataset'` 必须提供 `preflight_dataset(dataset, params)`，集合只执行一次，`context.episode=None`。逐轨迹最低输入写 `episode_requirements`，可另提供 `preflight_episode(episode, params)`；框架先筛轨迹，再对该方法自己的集合调用集合预检。`required_inputs/data_requirements/input_requirements` 属于 episode 方法，不能混用为 dataset 整体声明。

episode 方法的 `data_requirements.channel_param` 可以引用字符串或字符串数组参数；数组输入会解析每个逻辑名、拒绝解析后重复的源通道，逐项检查并保存实际绑定。`min_channels` 声明最低数量，不代替跨通道共同 clock 的检查。省略可选通道数组时，推荐端保留 `deferred` 条目；算法必须提供 `preflight` 处理默认范围，否则明确缺输入。可通过 `input_bindings` 钩子记录默认选择的范围，指标池已有此实现。

例如一个方法要求末端米制反馈，另一方法只要求头相机，两者会得到不同 eligible_episodes。工作流质量门槛先行；method_select 只可继续缩小，不能把未通过质量或输入检查的轨迹加回来。

## 变换与读取器

编辑／增强返回 `output / params / source_mapping`；必须是新版本，父数据指纹精确匹配，经逐项校验才能进入质量复检。当前只支持 identity 行映射，不把裁剪或重采样假写成 identity。

读取器注册组是 `edc.readers`，实现 `can_open(path)` 和 `open_dataset(path, profile)`，返回可真实读取的 Dataset。参考 [读取器模板](../../examples/plugins/reader)、[算法模板](../../examples/plugins/algorithm) 与根目录 [验收任务](../../FRIEND_VALIDATION.md)。
