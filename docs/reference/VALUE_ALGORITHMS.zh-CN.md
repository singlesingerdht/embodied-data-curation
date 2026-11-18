# 内置价值算法

[English](VALUE_ALGORITHMS.md) · [算法运行](../how-to/RUN_AND_SELECT.zh-CN.md) · [算法接入与内化](../how-to/ALGORITHM_INTEGRATION.zh-CN.md)

价值算法保留原分布、事件和实验估值，不生成跨算法总分，也不自动判为质量通过。两个入口必须显式选择，九项质量算法的默认集合和顺序不变。

| ID | 粒度 | 实际计算 |
|---|---|---|
| Initial_State_Distribution | 数据集 | SAM3 初始帧检测、二维分布；显式开启的深度几何 |
| Progress_Stagnation_Attribution | 轨迹 | GRM 进度、回退／停滞归因，显式启用的 v8 建议 |

## 安装与调用

基础安装不带模型库和权重，不下载模型、不初始化 GPU。模型环境中安装同一份 EDC wheel。两项均支持用 python 参数选择独立进程；执行前核对源码指纹。初始分布未指定 python 时保留当前进程执行。统一安装、版本组合和检查见[模型环境与依赖](MODEL_ENVIRONMENTS.zh-CN.md)。

| 方法 | 另行准备的依赖 |
|---|---|
| 初始分布 | SAM3、Torch、TorchVision、Pillow，本地 SAM3 权重 |
| GRM | Torch、Transformers、vLLM、OpenCV、MoviePy、Matplotlib、本地 GRM；自动分段另需 google-genai |

缺资源返回诊断，不以空结果代替计算。来源和许可见随包 SOURCE.json；依赖许可不自动覆盖修改代码、模型和数据，未核实的发布授权仍为未核实。

以下路径均为**示例占位路径**。机器人卡片需先编译，完整 profile 可直接使用；见[Python 接口](../how-to/RUN_AND_SELECT.zh-CN.md#python-api)。

~~~python
from pathlib import Path
import yaml
import edc
profile = yaml.safe_load(Path('/path/to/profile.yaml').read_text())
algorithm_id = 'Initial_State_Distribution'
parameters = yaml.safe_load(Path('/path/to/algorithm-params.yaml').read_text())
with edc.open_dataset('/path/to/dataset', profile=profile) as dataset:
    recommendation = edc.recommend(dataset, params={algorithm_id: parameters})
    result = edc.run(dataset, output='/path/to/new-results',
        algorithm_ids=[algorithm_id], params={algorithm_id: parameters})
~~~

参数遵守各 AlgorithmSpec.parameter_schema，推荐和执行使用同一套参数。

## 初始状态分布

最小参数：

~~~yaml
channel: head_rgb
checkpoint: /path/to/sam3.pt
comparison_space: fixed_camera_same_workspace
objects:
  - name: cup
    prompt: cup
    confidence_threshold: 0.5
frame_index: 0
grid_size: 8
device: cuda
~~~

comparison_space 是用户确认的相机／场景分组，不自动校正机位。每类保留检测、网格概率、归一化熵、有效轨迹比例及 100 × normalized_entropy × valid_episode_ratio 原评分，总分为类别平均。无检测和处理失败保持原分母口径；失败保留错误、selector 和证据，不能冒充完整评估。输出首帧、检测、热力图、统计和来源。正式 ID 为 Initial_State_Distribution，无旧插件 ID 静默别名，历史结果不改名。

### 可选空间状态

显式 spatial 才计算几何，**不参与二维分数**：

~~~yaml
spatial:
  source: sensor_depth  # 或 monocular_estimate，后者另需 model 本地路径
  unit: m              # 明确 m 或 mm
  registered_to_rgb: true
  registration_id: calibrated_camera_pair
  intrinsics: {fx: 500, fy: 500, cx: 320, cy: 240}
  episodes:
    stable_episode_id:
      path: /path/to/registered-depth.npy
      rgb_frame_index: 0
      depth_source_index: 0
      correspondence_evidence: verified_capture_mapping
~~~

内参数字仅示意格式，必须替换真实标定。二维深度图严格对应 RGB 像素，不猜单位、不缩放配准。输出相机坐标、支撑面质量、物体高度和深度来源。单目估计记录模型指纹，不称为传感器测量。深度／平面失败保留二维结果，标记空间未完成。几何测试不证明具体标定或单目尺度准确。Sobol 补覆盖为独立本地插件，不进入此评分或框架安装包。

## 任务进度与停滞归因

EDC 提供三路原始 RGB 图片，`views` 将 `cam_high`、`cam_left_wrist`、`cam_right_wrist` 绑定到实际通道。`Episode.iter_frames()` 直接读取图片或解码视频；两种存储都向 GRM 提供相同的参考图与前后图像组，仅按需缓存原始图片为 PNG。`frame_interval: 10` 始终表示每隔十个参考帧取样，与播放帧率无关。

以 `cam_high` 为参考，优先使用明确的逐行对应关系；否则在共同 clock 或显式时钟映射下选最近的真实画面，距离相同选较早帧。支持不同采样率、不同帧数、可变播放帧率及同一画面的重复引用。超出覆盖范围保留缺口，不用最后一帧填满；帧数相同不等于同步。`input-mapping.json` 保存轨迹 ID、selector、通道、原始帧号、分别记录的源时间／容器时间／播放时间、配对依据、偏差和覆盖信息。工作流保留质量放行记录与数据指纹，独立调用不宣称已有质量放行。

原生结果保留 `subtask_<序号>_<时间戳>/pred_vllm.json` 和原始模型响应，以及汇总 `pred_vllm.json`、`evaluation_summary.json`、`progress_chart.png`、`assessment_report.txt`。`attribution_result.json` 和 `attribution_report.txt`，逐项关联视觉候选、子任务、原始帧、源秒数、物理结论和缺失依据。归因分别使用各子任务曲线，不把新子任务的进度重置用作上一任务的回退证据。

`export_video` 默认 `false`；设为 `true` 才生成各子任务及汇总的 `reward_vis.mp4`，保留左腕／头部／右腕画面和进度变化、累计进度图。视频以 4 FPS 展示采样点；调整 `frame_interval` 会改变所选画面和视频长度，不改变这一展示帧率。画面对应 `input_after_frame_index`，`visualization.json` 记录导出状态；导出异常保留中间结果和图片。

`keep_frame_cache` 默认 `false`。正常处理结束后（包括物理评估未完成），EDC 仅清理本次运行缓存的输入 PNG，保留原始数据、曲线图及已导出视频。`input-mapping.json` 保留源帧号、selector、RGB 校验值和 `edc-frame:` 引用，仍可定位原画面。设为 `true` 可保留缓存；推理、归因或导出发生异常时也保留缓存供排查。没有可靠源时间时仍可计算视觉进度，但没有停滞记录不代表已经检查停滞。

参数包含 `model`、`task`、`physical_signals`，以及互斥的 `subtasks` 或 `segmentation`。每个显式子任务填写 `step`、`query`，并选择一种区间：

- `clip_start_sec`／`clip_end_sec`：兼容原秒区间；可选 `subtask_time_basis: playback|source`。未填时，有原始视频播放轴则用播放秒，否则用相对首帧的源时间秒数，并保存实际选择。
- `clip_start_frame`／`clip_end_frame`：原始参考帧的半开区间 `[start, end)`。只有图片顺序、没有可靠时间时使用；不能与秒区间混填。

例如，以下参数选择前 100 个参考帧；物理绑定尚未填写时，仍可计算视觉进度：

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

## 证据与失败

EDC 生成 edc_record_v1、catalog.json 和 artifacts。数据集方法通常写 artifacts/dataset/，逐轨迹位置以文件清单为准。模型初始化前保存 provenance.json：参数、源码／模型指纹、依赖和来源。初始化或后续失败保留已有证据；来源读取／写入失败不启动模型。processing_error、未完成、使用建议分离，失败不能改为通过或自动排除。

## HTML 结果报告

`edc run` 默认生成简洁的 `report.html`：算法、对象、原生分数、判定、执行状态、简短理由和详细结果链接。通过／未通过沿用算法已有判定；未配置通过阈值时显示“未设判定”，缺信息则显示“未完成”。曲线、热力图、视频和候选明细保留在原始输出中，不在 HTML 展开，也不为报告额外生成视频。`catalog.json` 指向各次运行的 HTML；`--no-html` 可关闭 HTML，JSON 与证据仍保留。分享报告时保留整个输出目录，以保持相对链接有效。
