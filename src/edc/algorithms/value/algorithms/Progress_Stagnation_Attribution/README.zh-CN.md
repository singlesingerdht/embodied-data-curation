# Progress_Stagnation_Attribution

[English](README.md)

通过 EDC AlgorithmSpec + run(context, params) 调用，环境安装及调用示例见框架价值算法参考。SOURCE.json 保留来源校验值及未核实许可；模型和数据另行核查。

## 从准备模型到完整归因

完整运行需要三视角画面、子任务区间、可对齐的真实时间，以及左右臂的夹爪开合、负载和末端运动反馈。下面按已有子任务标注运行 GRM 和物理归因，不使用远程自动分段，也不启用 v8 筛选。模型下载完成不等于输入已准备完整。

### 1. 安装同一版本的框架与模型环境

使用包含本算法的 EDC 源码。以下均为示例路径，输出目录应为新目录。主环境使用 Python 3.10 或以上；模型环境需要 Linux x86_64、Python 3.12 和可供 Torch/vLLM 使用的 NVIDIA GPU。模型显存需求取决于图片尺寸和运行参数，这里不承诺最低显存。

```bash
python -m pip install build
python -m build --wheel --outdir /path/to/edc-dist
python -m pip install '/path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl[formats,value-inputs]'

edc env create vision --with progress \
  --python /path/to/python3.12 \
  --venv /path/to/envs/progress \
  --edc-wheel /path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl
edc env check --venv /path/to/envs/progress --imports
edc algorithms
```

列表应包含 `Progress_Stagnation_Attribution`。模型环境固定 Torch 2.8.0、Transformers 4.57.1、vLLM 0.11.0；不要用通用 Qwen 权重替代 GRM，也不要在创建环境后随意升级 Transformers。环境检查不会启动模型。

### 2. 下载指定 GRM 权重

使用 [Robo-Dopamine-GRM-2.0-8B-Preview](https://huggingface.co/tanhuajie2001/Robo-Dopamine-GRM-2.0-8B-Preview)，下面固定模型修订，下载整个模型目录，包含分片权重、配置、分词器和图片处理资源。下载方式见 [Hugging Face 文档](https://huggingface.co/docs/huggingface_hub/guides/download)。

```bash
/path/to/envs/progress/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="tanhuajie2001/Robo-Dopamine-GRM-2.0-8B-Preview",
    revision="6521e2661c265b4c5561fd21fa326a975b323718",
    local_dir="/path/to/models/grm",
)
PY
```

需要使用 Hugging Face Mirror 时，在同一个下载终端先设置 `export HF_ENDPOINT=https://hf-mirror.com`，模型 ID 和修订保持一致。示例使用独立模型目录；已有完整本地权重可以直接复用。模型来源和许可按模型卡核对，框架不会将权重打入安装包。

### 3. 准备数据和完整机器人配置

复制[完整双臂配置示例](../../../../../../configs/robots/progress_example.yaml)，按实际数据填写后保存为 `/path/to/robot-progress.yaml`。这是输入格式范例，不是某一真实机器人的标定；不能把其中示意的确认声明当成自己的标定证据。已有机器人配置应保留原通道声明，补充相应算法参数。

示例使用一个轨迹目录，包含 `trajectory.json`、`head_rgb.mp4`、`left_rgb.mp4`、`right_rgb.mp4`。JSON 为 `{"data": [...]}`；每行具有以下字段：

| 字段 | 含义与形状 |
|---|---|
| `timestamp` | 记录的采集时间，秒；与声明的视频逐帧映射对应 |
| `left_gripper` / `right_gripper` | 标量开口距离，cm，数值越大越张开 |
| `left_effort` / `right_effort` | 已确认的关节电流数组，A；示例取整个数组的 RMS 作为显式负载代理 |
| `left_xyz` / `right_xyz` | 各自固定坐标系中的末端反馈位置 `[x, y, z]`，m |

仅当采集协议确实声明每帧对应一行时使用示例的 `video_frame_time: rows`；不能根据帧数相同推断这一关系。使用其他格式或不同采样率时，沿用对应数据格式与机器人配置的源时间/时钟映射。原始图片序列同样可用，无需先编码视频。

配置已经写全左右臂的 `physical_signals`，包含夹爪、臂负载、显式手部负载代理和末端位置。按实际记录替换字段、单位、开合方向及确认依据；`joint_effort` 这个名字本身不能证明是电流。若实际是力矩，应将负载声明改为 `unit: N*m`、`quantity: torque`，并显式提供适用的两个力矩阈值，不能直接继承 A 阈值。

示例的两个子任务是 `[0, 100)` 和 `[100, 200)`，仅说明区间格式。替换为本条轨迹真实的任务描述与标注；视角覆盖和物理信号必须覆盖需要评估的范围。当前实现会检查左右两侧，不能用伪造的全零信号补齐未记录的一侧。

**当前仓库不提供可公开下载的、带完整物理信号与标定的真实演示数据。**因此上面是完整配置范例，需要搭配自己的匹配数据；它不是下载模型后即可复现某条真实演示结论的数据包。只有画面的上游 GRM 演示可以展示视觉进度，不能代替物理归因样例。

### 4. 检查输入并执行

```bash
edc robot check --robot /path/to/robot-progress.yaml --data /path/to/episode
edc run /path/to/episode \
  --robot /path/to/robot-progress.yaml \
  --algorithm Progress_Stagnation_Attribution \
  --output /path/to/new-results
```

`robot check` 检查声明的通道能否读取，不能替代实际归因的完成检查。`edc run` 自动使用配置中的独立 Python 环境，不需要手工切换到模型环境。

### 5. 确认得到的是完整结果

先打开输出根目录的 `report.html`，通过 `catalog.json` 找到对应记录与证据。完整运行应同时满足：记录 `status: completed`，原生结果 `assessment_complete: true`，`components.segmentation`、`visual`、`physical` 均为 `completed`。本示例的 `filter` 应为 `not_requested`，因为没有选择 v8。

原生输出包含每个子任务的进度及模型响应、汇总曲线 `progress_chart.png`、`evaluation_summary.json`、`attribution_result.json` 和可读的 `attribution_report.txt`。归因报告给出原始帧/时间、候选问题、物理结论和依据；“没有检测到事件”也可以是完整结果。检测到问题并非运行失败，也不应预先承诺某个标签一定出现。

缺少输入或标定时保留已有结果并标记未完成；只看到 JSON、HTML 或命令退出成功不代表完成归因。视频默认关闭；需要复核视频时设置 `export_video: true`。正常结束后默认删除本次输入图片缓存，保留源映射和结果证据。


### X1 Pro 非 G 夹爪倒茶示例

[完整 X1 Pro 示例配置](../../../../../../configs/robots/x1pro_progress_example.yaml)使用 `faceImg`、`leftImg`、`rightImg` 三视角和 JSON 中的 `timestamp`、`follow_left/right_gripper`、`follow_left/right_joint_effort`、`follow_left/right_position`。夹爪为 cm、数值越大越张开；effort 按本例采集约定声明为 N·m，使用全部七维的 RMS 作为代理，固定变化阈值分别为 0.3／0.2 N·m。这些声明适用于本例，不能推广到所有 X1 Pro 夹爪或导出格式。

子任务取自 `pourtea_lzy_0720@MASTER_SLAVE_MODE@2026_07_20_10_55_07` 的人工标注：原 step 1 为 `[332, 908)` 抓烧水壶，原 step 5 为 `[2808, 4337)` 抓茶壶并向第一只杯子倒茶。只评估这两个区间，不表示整条轨迹全部任务已评估。原生 `subtask_0`／`subtask_1` 按本次选中顺序编号；原标注的 step 与区间保存在 `components.segmentation.intervals`。对应的原 JSON 与三个同名 MP4 应位于轨迹目录；`video_frame_time: rows` 表示采集协议明确的一行对应一帧关系。使用其他轨迹时必须替换真实标注及适用的信号说明。

```bash
cp configs/robots/x1pro_progress_example.yaml /path/to/robot-progress.yaml
# 编辑 model 和 python 的本地路径；其他数据还需更新信号声明及子任务。
edc robot check --robot /path/to/robot-progress.yaml --data /path/to/pourtea-episode
edc run /path/to/pourtea-episode \
  --robot /path/to/robot-progress.yaml \
  --algorithm Progress_Stagnation_Attribution \
  --output /path/to/results/progress
```

仓库提供配置与运行步骤，不附带这条真实轨迹；需由数据持有者在本机提供。运行后按上面的完成条件检查，并对照 `attribution_report.txt` 中的原始帧／源时间查看画面。GRM 回退候选不等于已确认空抓；七维 RMS 也可能受机械臂运动影响。`assessment_complete` 表示所选分支已执行且证据满足要求，不能证明每个归因标签准确。误报和漏报应通过画面复核，不以修改阈值强行复现某个标签。


## 任务进度与停滞归因

EDC 提供三路原始 RGB 图片，`views` 将 `cam_high`、`cam_left_wrist`、`cam_right_wrist` 绑定到实际通道。`Episode.iter_frames()` 直接读取图片或解码视频；两种存储都向 GRM 提供相同的参考图与前后图像组，仅按需缓存原始图片为 PNG。`frame_interval: 10` 始终表示每隔十个参考帧取样，与播放帧率无关。

以 `cam_high` 为参考，优先使用明确的逐行对应关系；否则在共同 clock 或显式时钟映射下选最近的真实画面，距离相同选较早帧。支持不同采样率、不同帧数、可变播放帧率及同一画面的重复引用。超出覆盖范围保留缺口，不用最后一帧填满；帧数相同不等于同步。`input-mapping.json` 保存轨迹 ID、selector、通道、原始帧号、分别记录的源时间／容器时间／播放时间、配对依据、偏差和覆盖信息。工作流保留质量放行记录与数据指纹，独立调用不宣称已有质量放行。

原生结果保留 `subtask_<序号>_<时间戳>/pred_vllm.json` 和原始模型响应，以及汇总 `pred_vllm.json`、`evaluation_summary.json`、`progress_chart.png`、`assessment_report.txt`。`attribution_result.json` 和 `attribution_report.txt`，逐项关联视觉候选、子任务、原始帧、源秒数、物理结论和缺失依据。归因分别使用各子任务曲线，不把新子任务的进度重置用作上一任务的回退证据。

`export_video` 默认 `false`；设为 `true` 才生成各子任务及汇总的 `reward_vis.mp4`，保留左腕／头部／右腕画面和进度变化、累计进度图。视频以 4 FPS 展示采样点；调整 `frame_interval` 会改变所选画面和视频长度，不改变这一展示帧率。画面对应 `input_after_frame_index`，`visualization.json` 记录导出状态；导出异常保留中间结果和图片。

`keep_frame_cache` 默认 `false`。正常处理结束后（包括物理评估未完成），EDC 仅清理本次运行缓存的输入 PNG，保留原始数据、曲线图及已导出视频。`input-mapping.json` 保留源帧号、selector、RGB 校验值和 `edc-frame:` 引用，仍可定位原画面。设为 `true` 可保留缓存；推理、归因或导出发生异常时也保留缓存供排查。没有可靠源时间时仍可计算视觉进度，但没有停滞记录不代表已经检查停滞。

参数包含 `model`、`task`、`physical_signals`，以及互斥的 `subtasks` 或 `segmentation`。每个显式子任务填写 `step`、`query`，并选择一种区间：

- `clip_start_sec`／`clip_end_sec`：兼容原秒区间；可选 `subtask_time_basis: playback|source`。未填时，有原始视频播放轴则用播放秒，否则用相对首帧的源时间秒数，并保存实际选择。
- `clip_start_frame`／`clip_end_frame`：原始参考帧的半开区间 `[start, end)`。只有图片顺序、没有可靠时间时使用；不能与秒区间混填。

以下仅为视觉试跑参数，选择前 100 个参考帧。`physical_signals: {}` 不会完成物理归因；完整运行请使用上面的配置示例：

~~~yaml
views: {cam_high: head_rgb, cam_left_wrist: left_rgb, cam_right_wrist: right_rgb}
model: /path/to/local-grm
python: /path/to/model-environment/bin/python
task: pick and place
physical_signals: {}
frame_interval: 10
subtasks:
  - {step: 0, query: pick and place, clip_start_frame: 0, clip_end_frame: 100}
filter_policy: none
~~~

上述路径和通道名均为示例。通过 `edc run` 调用时，将参数放在机器人 YAML 的 `checks.Progress_Stagnation_Attribution` 下；工作流则放在阶段的 `params` 中。命令示例：

~~~bash
edc run /path/to/data --robot /path/to/robot-with-progress.yaml --algorithm Progress_Stagnation_Attribution --output /path/to/new-results
~~~

停滞时长、速度及物理事件窗口使用源时间；播放时间不作为采集同步证据。只有顺序的图片支持视觉进度，不能据此计算秒级停滞或物理同步。`max_frame_gap_sec` 已废弃，兼容读取但不生效，并记录提示；质量要求仍由质量阶段管理。本算法不新增固定 FPS、最大相邻帧间隔或源时间等于播放时间的门槛。

物理绑定为 left/right → role → 配置，提供 channel、field、反馈语义、可选单位转换、max_gap_sec 和 indices。角色支持 hand_position、hand_load、arm_load、ee_position、arm_velocity、joint_position。机器人辅助信号可显式登记 quantity、source_kind、calibration_status: confirmed 和 calibration_reference；未知单位保持未知。某角色读取失败时保留其他可用信号，末端位置通过 EDC 转为米；插值范围不作为相机帧率质检门槛。

手部默认 `grip_mode: references`，使用已确认的 `open_reference`／`closed_reference`。也可配置 `grip_mode: native_relative`、`opening_direction: increasing` 和 `direction_reference`，复用原始标量夹爪分位数／迟滞开合规则，不做全开／全闭归一化。保留配置中的输入尺度和原 .01 下限，观测最大最小值不冒充物理行程标定。默认两种方式均要求机器人语义、单位和标定已确认，算法参数不能升级未知信息。如需臂负载作为手部负载代理，显式填写 `hand_load: {proxy_of: arm_load, reference: <适用依据>}`，结果保留代理身份，不将臂 effort 改称实测手指电流。确认以安培记录的电流信号直接按 RMS 使用，无需另填负载缩放参数。固定规则可选配置为 `load_rules: {unit: A, close_delta: 0.3, stagnation_empty_delta: 0.2}`：闭合前后变化使用 0.3 A，停滞中的空抓变化使用 0.2 A；不填写时采用这些默认值。夹爪分位数、负载基线、标准差及尖峰门限由算法计算，不提供手填覆盖配置。负载是电流代理，不是物体重量；`joint_effort` 必须由采集协议确认单位，未知值或力矩不会自动变成电流。旧 `delta_threshold` 仅兼容原有显式缩放，运行时提示迁移并记录实际尺度，不能与新 `load_rules` 混用。结果保存有效规则、信号来源及闭合前后负载，便于核对判断依据。

显式设置 `evidence_policy: allow_provisional` 可使用机器人配置中 `calibration_status: pending` 且已写明单位、物理含义、反馈身份与 `calibration_reference` 的夹爪／负载信号进行条件归因。默认 `confirmed_only` 仍要求确认。条件归因保留原始标定状态、单位假设、负载代理身份和逐候选 `provisional` 标记，`assessment_complete` 仍为 false，不进入工作流完成集合，也不执行主动抓取扫描或 `training_filter_v8` 筛选建议；使用已有明确子任务区间，自动分段要求不变。未知单位、规则量纲不匹配、指令或缺失时钟不会因此被接受。固定规则及自动统计方式均不变。

负载规则也支持已声明的关节力矩 `N*m`，此时必须显式给出 `load_rules.unit: N*m`、`close_delta` 和 `stagnation_empty_delta`，且信号声明 `quantity: torque`。阈值由适用方法／使用者提供，不继承电流默认值；RMS 保留力矩数值，不通过电机常数换算成电流。历史结果字段 `cur_*` 为兼容保留，实际量纲以 `load_rules` 和 `load_sources` 为准。力矩分支只执行回退／停滞归因，未标定的主动抓取扫描和 `training_filter_v8` 电流规则不会据此启用。

`filter_policy` 默认 `none`；显式选择 `training_filter_v8` 才产生建议，不删除、裁剪或启动训练。主动扫描另需手部位置及负载的 `v8_native_scale`：scale、offset、reference、verified: true，表示已验证的原规则量纲映射。时长窗口按源时间计算，恢复规则中的样本计数及数值阈值保持原义。保留失败尝试、后续恢复、版本、区间和原始响应；恢复不撤销失败，失败区间不吞掉下一次夹爪动作。

Gemini 自动分段需显式配置 method: gemini、model、api_key_env、allow_remote_upload: true、description、任务 subtasks。EDC 按源时间生成专用 30 FPS 展示视频，在编码分辨率内保持观测时长，保存 `segmentation-video/video-mapping.json`。特征图、事件日志使用同一源相对展示时间轴，模型区间映射回原始参考帧和源秒数。30 FPS 仅是内部编码参数，不是输入门槛。双臂需标定开合、米制末端、安培臂负载、弧度关节位置和明确分段量纲；未授权不上传，缺少证据不虚构分段。

输出分别登记分段、视觉评估、物理归因和所选筛选分支。**完整归因才设置 `assessment_complete: true`。**缺时间、信号、标定或覆盖时保留已有视觉结果，完整估值仍未完成，工作流不把该轨迹加入已完成估值集合。真实执行异常仍记录为处理失败；模型初始化前保存源码、配置与模型指纹，失败后的缓存图片和中间证据仍由 `catalog.json` 发现。`source_time_sec` 及 hop 源时间均相对首帧，原始帧绑定保留。接口／规则测试不代表真实准确率或训练收益。

## HTML 结果报告

`edc run` 默认生成简洁的 `report.html`：算法、对象、原生分数、判定、执行状态、简短理由和详细结果链接。通过／未通过沿用算法已有判定；未配置通过阈值时显示“未设判定”，缺信息则显示“未完成”。曲线、热力图、视频和候选明细保留在原始输出中，不在 HTML 展开，也不为报告额外生成视频。`catalog.json` 指向各次运行的 HTML；`--no-html` 可关闭 HTML，JSON 与证据仍保留。分享报告时保留整个输出目录，以保持相对链接有效。
