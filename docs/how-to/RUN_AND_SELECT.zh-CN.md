# 算法运行

[返回主页](../../README.zh-CN.md) · [English](RUN_AND_SELECT.md) · [内置质量算法参考](../reference/ALGORITHMS.zh-CN.md)

通过推荐确认输入条件，选择算法并运行检查。算法参数见[内置质量算法参考](../reference/ALGORITHMS.zh-CN.md)。

<a id="selection"></a>
## 1. 查看推荐

推荐以当前输入、单位资格、语义、时间、参数及依赖为依据。`available` 表示预检满足；`partial` 表示可检查明确子集，同时列出未覆盖范围；`unavailable` 表示缺少必需条件。

```bash
python examples/quickstart/recommend.py /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml
```

脚本输出 `edc.recommend()` 的完整 JSON：每条轨迹的 `algorithms` 中有 `algorithm.id`、`availability`、`reason`、`channel_bindings` 与 `parameters`；多实例还保留 `instances`。

## 2. 选择并执行

选择两项执行：

```bash
edc run /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --algorithm Structure_Check --algorithm Numeric_Check \
  --output /path/to/results/selected
```

`run` 保留显式算法顺序。未指定算法时，默认尝试完整与部分可运行的质量项，不可运行项保留原因。`batch` 对每条轨迹运行已登记的质量集合，可并行处理轨迹，不能使用 `--algorithm`：

```bash
edc batch /path/to/dataset \
  --robot /path/to/robot.yaml --output /path/to/results/report.html \
  --keep /path/to/results/details --jobs 1
```

`--keep` 保存每条轨迹的完整结果与证据；目录须新建。`--jobs` 控制轨迹并发，默认16，首次使用建议1。`--tmp` 可指定临时目录。只列注册算法用 `edc algorithms`；没有独立的推荐命令行入口或 `edc config export` 命令。完整命令行参数见[命令行参考](../reference/CLI.md)。

<a id="python-api"></a>
### 使用同一套参数（Python 接口）

推荐与执行应传入同一套生效参数。机器人卡片需先编译；完整配置直接读取。下面例子与主页 MCAP 配置一致：

```python
from pathlib import Path
import yaml
import edc

robot = Path('configs/robots/x1pro_mcap.yaml').resolve()
config = yaml.safe_load(robot.read_text(encoding='utf-8'))
if 'robot' in config and 'schema_version' not in config:
    profile, params, _ = edc.compile_card(config, base=robot.parent)
else:
    profile, params = config, {}

with edc.open_dataset('/path/to/dataset/episode.mcap', profile=profile) as dataset:
    recommendation = edc.recommend(dataset, params=params)
    selected = ['Structure_Check', 'Numeric_Check']
    result = edc.run(dataset, output='/path/to/results/api',
                     algorithm_ids=selected,
                     params={k: v for k, v in params.items() if k in selected})
```

卡片参数只写在 `checks.<algorithm_id>`，不另用参数文件覆盖；YAML 改过后 `edc batch` 会要求重新签名。范围参数如 `arms: [left]` 与输入 `channels` 依具体方法声明使用。切换范围后推荐与执行都要使用更新的参数。[机器人声明与本次检查范围](../reference/DATA_AND_ROBOTS.zh-CN.md#scope)

<a id="results"></a>
## 3. 阅读结果

HTML 提供整体结论、逐条一览、按算法看问题和逐条详情。先定位有问题的轨迹与算法，再看本项原因；需要完整指标和证据时打开 JSON 或使用 `--keep` 保留的输出。真实 MCAP 的参考结果见[快速开始](../tutorial/QUICKSTART.zh-CN.md#results)。

单项判断为通过、复核、可救或排除；没有检查条件和执行失败与质量判断分开。

`run` 通过 `catalog.json` 发现 `_runs/<run_id>` 下的配置、`records.jsonl`、`quality.json`、错误与证据。保留原方法指标，不生成跨算法或跨臂总分。输出对象说明见[输出对象与接口参考](../reference/OUTPUTS.zh-CN.md)。

## 下一步

- 按报告筛选并导出数据：[筛选与导出](SELECTION.md)。
- 想让某项检查更严格或跳过某项检查：[内置质量算法参考](../reference/ALGORITHMS.zh-CN.md) 与[检查范围与证据参数参考](../reference/EVIDENCE_AND_SCOPE.md)。
