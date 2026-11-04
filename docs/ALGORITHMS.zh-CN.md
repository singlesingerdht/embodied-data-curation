# 算法使用指南

[返回主页](../README.zh-CN.md) · [English](ALGORITHMS.md) · [数据配置](DATA_AND_CONFIGURATION.zh-CN.md)

<a id="selection"></a>
## 推荐、选择与执行

推荐以当前输入、单位资格、语义、时间、参数及依赖为依据。`available` 表示预检满足；`partial` 表示可检查明确子集，同时列出未覆盖范围；`unavailable` 表示缺少必需条件。预检不等于实际读取全部内容，也不等于数据质量通过。

```bash
python examples/quickstart/recommend.py /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml
```

脚本输出 `edc.recommend()` 的完整 JSON：每条轨迹的 `algorithms` 中有 `algorithm.id`、`availability`、`reason`、`channel_bindings` 与 `parameters`；多实例还保留 `instances`。不要仅看算法名称而忽略原因和范围。

选择两项执行：

```bash
edc run /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --algorithm Structure_Check --algorithm Numeric_Check \
  --output /path/to/results/selected
```

`run` 保留显式算法顺序。默认尝试完整与部分可运行的质量项，不可运行项保留原因。`batch` 对每条轨迹运行已登记的质量集合，可并行处理轨迹，不能使用 `--algorithm`：

```bash
edc batch /path/to/dataset \
  --robot /path/to/robot.yaml --output /path/to/results/report.html \
  --keep /path/to/results/details --jobs 1
```

`--keep` 保存每条轨迹的完整结果与证据；目录须新建。`--jobs` 控制轨迹并发，默认16，首次使用建议1。`--tmp` 可指定临时目录。只列注册算法用 `edc algorithms`；没有独立的推荐 CLI 或 `edc config export` 命令。

### Python API

推荐与执行应传入同一套生效参数。机器人卡片需先编译；完整 profile 直接读取。下面例子与主页 MCAP 配置一致：

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

卡片参数写在 `checks.<算法ID>`；`--params /path/to/params.json` 接受 `{算法ID: {参数: 值}}`，覆盖对应算法参数。范围参数如 `arms: [left]` 与输入 `channels` 依具体方法声明使用。切换范围后推荐与执行都要使用更新的参数。[单／双臂](DATA_AND_CONFIGURATION.zh-CN.md#robot)

## 九项内置质量算法

以下说明当前输入与主要行为；详细实现分别链接到源码。共同结果表示见[输出规范](DEVELOPMENT.zh-CN.md#outputs)。所有参数以安装版算法元数据及源码为准，示例参数不作为跨设备通用标定。

<a id="structure-check"></a>
### Structure_Check · 结构检查

- **输入：** 通道目录、已声明相机要求、时长与机器人部件。
- **参数：** 卡片 `data.min_duration_s` 默认3秒；缺失要求由配置决定。
- **输出／判断：** 结构评分、缺失项与时长问题；缺少要求内容或过短会影响结论，过短轨迹可阻止其余检查。
- **边界：** 检查文件结构不证明物理语义、任务成功或传感器健康；缺 `camera_info` 与缺相机不是同一条件。
- [实现](../src/edc/algorithms/quality/algorithms/Structure_Check/)

<a id="numeric-check"></a>
### Numeric_Check · 数值检查

- **输入：** 已登记数值；物理子项另需确认语义、SI 单位、标定和对应阈值。
- **参数：** `freeze_min_duration_s` 默认5；卡片可提供 `range`、URDF 关节限位、`max_joint_velocity`；`joint_stuck` 需要同臂关节和末端反馈。
- **输出／判断：** 逐字段有限值、超限与冻结事件；NaN／Inf 或明确超限可排除，冻结线索需复核；关节卡住且末端运动可给出可救项及处理建议。
- **边界：** 未配置的物理子项不算检查通过。IMU 等已接入子检查属于本算法；未知 effort 不解释为力矩，不把指令当反馈。
- [实现](../src/edc/algorithms/quality/algorithms/Numeric_Check/)

<a id="action-smoothness"></a>
### Action_Smoothness · 动作平滑度

- **输入：** 所选臂的末端 XYZ 反馈，单位m；记录值或显式 URDF 正运动学结果，以及该通道可用时间。
- **方法：** 按通道自身时间分段，用各段中位间隔建立等间隔网格；频域提取保持段振动加速度。没有默认将头部相机作为参考，也不使用固定帧数的 jerk 方法。
- **参数：** `band_hz: [2, 10]`、`band_rate_ratio: 0.4`、`min_rate_hz: 10`；`still_speed_m_s: 0.01`、`still_min_s: 0.3`；`still_review_m_s2: 1.0`。
- **输出／判断：** 各臂保持段 RMS 加速度（m/s²）、事件和冻结线索；只有通过、复核、不适用。运动段数值供参考，不用于排除。
- **边界：** 频段上限受采样率限制；复核线仍需目标场景标定。缺时钟单位、采样不足或无保持段时不能声称完成振动检查。
- [方法与全部参数](../src/edc/algorithms/quality/algorithms/Action_Smoothness/README.md)

<a id="check-timestamp"></a>
### Check_Timestamp · 时间戳检查

- **输入：** 有效且单位明确的时间；跨通道配对另需可比较时间和参考输入。
- **参数：** `shared_row_max_gap_s` 默认按中位行间隔的3倍；`scoring` 调整本方法已有评分参数。
- **输出／判断：** 时间间隔、断档和可用配对指标；可只检查单路／共享行时间，不把该范围报告为跨设备同步通过。
- **边界：** 缺独立采集时间、单位或 clock 时，推荐列明部分可检查范围。
- [实现](../src/edc/algorithms/quality/algorithms/Check_Timestamp/)

<a id="sync-check"></a>
### Sync_Check · 采集同步

- **输入：** 多路独立、有效的采集时间，满足共同 clock 或显式映射条件。
- **参数：** `primary` 可选择参考通道；配置本方法支持的时间条件后再运行。
- **输出／判断：** 偏差、抖动及已有同步评分；固定偏移诊断与可救项保留映射依据。
- **边界：** 共用一条行时间不能验证多设备采集同步；报告偏差不独立证明其设备或网络原因。参考通道名称本身不构成 clock 证据。
- [实现](../src/edc/algorithms/quality/algorithms/Sync_Check/)

<a id="root-cause"></a>
### Root_Cause · 时间异常归因

- **输入：** 可比较的多路时间及来源分组；时间条件与跨通道检查一致。
- **参数：** `families` 描述哪些通道属于同组来源。
- **输出／判断：** 断档等异常的共同出现和关联线索，按现有规则形成复核项。
- **边界：** 这是定位线索，不是设备故障的确定诊断；单一共享行时间不足以独立归因。
- [实现](../src/edc/algorithms/quality/algorithms/Root_Cause/)

三个时间算法的可选 `record_header_deviation_threshold_ms` 与原时间差证据见[专项说明](EVIDENCE_AND_SCOPE.md)。默认 `null`；补充证据不自动改分。

<a id="dedup-check"></a>
### Dedup_Check · 时间／序列重复

- **输入：** 原始时间值或支持的序列信息；共享时间仅检查一次并保留来源。
- **参数：** 按已选通道与算法声明执行，不把图像重复参数传给本方法。
- **输出／判断：** 重复计数、连续重复片段和已有重复评分。
- **边界：** 时间相同不证明图像或动作内容复制；缺时间的通道不能静默算作已检查。
- [实现](../src/edc/algorithms/quality/algorithms/Dedup_Check/)

<a id="reset-detection"></a>
### Reset_Detection · 复位检测

- **输入：** 所选臂反馈位置及同机器人、同字段的 Home 与正容差。
- **参数：** 卡片 `home` 可使用关节rad或末端m；双臂须保持同类型和相同卡片容差。完整 profile 也支持明确的逐通道标定。`split_segments` 用于按已有切片标记跳过复位。
- **输出／判断：** 开头／结尾与参考的偏差及复位判断，超出条件需复核。
- **边界：** 关节数、单位、零位或坐标系不匹配时，不能套用另一机器人的 Home；任务允许不同初末状态时需调整使用范围。
- [实现](../src/edc/algorithms/quality/algorithms/Reset_Detection/)

<a id="video-integrity"></a>
### Video_Integrity · 图像／视频完整性

- **输入：** 所选腕部及明确共享相机的可解码帧／视频。
- **参数：** `input_mode` 为 `auto`／`frames`／`video`；`sample_step` 默认5，`downscale` 控制检查尺度；`freeze_motion_review` 默认空映射。
- **输出／判断：** 黑白屏、模糊、异常画面、冻结等候选与计数；当前画面规则中某类问题占抽检帧30%及以上可排除，低比例仍可通过并附数量。评分与人工复核线索可以并存。
- **边界：** 图片没有容器指标；抽样结果不等于逐帧结果。冻结联合复核需要已配置反馈及共同 clock／显式映射，只补证据，不自动重判。[联合复核参数](EVIDENCE_AND_SCOPE.md)
- [实现](../src/edc/algorithms/quality/algorithms/Video_Integrity/)

<a id="results"></a>
## 阅读结果

HTML 提供整体结论、逐条一览、按算法看问题和逐条详情。先定位有问题的轨迹与算法，再看本项原因；需要完整指标和证据时打开 JSON 或使用 `--keep` 保留的输出。主页快速开始中的[真实 MCAP 参考结果](QUICKSTART.zh-CN.md#results)来自当前实现。

单项判断为通过、复核、可救或排除；没有检查条件和执行失败与质量判断分开。表格汇总不代替完整评估或工作流放行，未检查不能解释为通过。

`run` 通过 `catalog.json` 发现 `_runs/<run_id>` 下的配置、`records.jsonl`、`quality.json`、错误与证据。保留原方法指标，不生成跨算法或跨臂总分。输出对象说明见[开发指南](DEVELOPMENT.zh-CN.md#outputs)。

已有[数据复制／选择入口](SELECTION.md)和[工作流接口](WORKFLOWS.md)保留；价值评估、编辑和增强算法按需另行接入。
