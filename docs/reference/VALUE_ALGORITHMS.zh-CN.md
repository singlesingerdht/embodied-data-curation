# 内置价值算法

[English](VALUE_ALGORITHMS.md) · [算法运行](../how-to/RUN_AND_SELECT.zh-CN.md) · [算法接入与内化](../how-to/ALGORITHM_INTEGRATION.zh-CN.md)

价值算法保留原分布、事件和实验估值，不生成跨算法总分，也不自动判为质量通过。三个入口必须显式选择，九项质量算法的默认集合和顺序不变。

| ID | 粒度 | 实际计算 |
|---|---|---|
| Initial_State_Distribution | 数据集 | SAM3 初始帧检测、二维分布；显式开启的深度几何 |
| Progress_Stagnation_Attribution | 轨迹 | GRM 进度、回退／停滞归因，显式启用的 v8 建议 |
| Collision_Detection_Qwen3vl | 轨迹 | 四类安全事件、候选裁决与图像证据 |

## 安装与调用

基础安装不带模型库和权重，不下载模型、不初始化 GPU。模型环境中安装同一份 EDC wheel。三项均支持用 python 参数选择独立进程；执行前核对源码指纹。初始分布未指定 python 时保留当前进程执行。统一安装、版本组合和检查见[模型环境与依赖](MODEL_ENVIRONMENTS.zh-CN.md)。

| 方法 | 另行准备的依赖 |
|---|---|
| 初始分布 | SAM3、Torch、TorchVision、Pillow，本地 SAM3 权重 |
| GRM | Torch、Transformers、vLLM、OpenCV、MoviePy、Matplotlib、本地 GRM；自动分段另需 google-genai |
| 安全检测 | Torch、Transformers、SAM3、CoTracker3、DINOv2、Qwen3-VL；纯 RGB 数据可选 DA3 深度准备 |

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

### 安装环境和下载 SAM3

以下命令在框架源码目录执行，所有 `/path/to/...` 都需要替换为自己的绝对路径。主环境需要 Python 3.10 或以上，模型环境需要 Linux x86_64、Python 3.12 和可用的 NVIDIA GPU。先构建同一个 EDC wheel，供主环境与模型进程安装：

```bash
python -m pip install build
python -m build --wheel --outdir /path/to/edc-dist
python -m pip install '/path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl[formats,initial-state]'
```

从 [SAM3 官方源码](https://github.com/facebookresearch/sam3) 构建固定修订的源码包。只构建 SAM3 wheel，不将源码或权重复制到 EDC 仓库：

```bash
git clone https://github.com/facebookresearch/sam3.git /path/to/sam3-source
git -C /path/to/sam3-source checkout 8f0b7f4d4e7eda2ed606ebde6702c93359ad01da
python -m pip wheel --no-deps /path/to/sam3-source --wheel-dir /path/to/source-wheels

edc env create vision --with initial-state \
  --python /path/to/python3.12 \
  --venv /path/to/envs/initial-state \
  --edc-wheel /path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source sam3=/path/to/source-wheels/sam3-0.1.0-py3-none-any.whl
edc env check --venv /path/to/envs/initial-state --imports
```

环境配置固定 Torch 2.8.0、TorchVision 0.23.0、Transformers 4.57.1 和 NumPy 1.26.4。创建新环境时可同时指定 `--with progress --with initial-state`，让两个算法共用该模型环境；各自配置中的 `python` 应指向它。基础安装不引入模型库或权重。

先在 [facebook/sam3 模型页](https://huggingface.co/facebook/sam3) 获得访问权限并接受适用条款，再登录下载。此实现使用原生 `sam3.pt`，不能用 Transformers 的 `model.safetensors` 替代：

```bash
/path/to/envs/initial-state/bin/hf auth login
/path/to/envs/initial-state/bin/python - <<'PYTHON'
from huggingface_hub import hf_hub_download
hf_hub_download(
    repo_id="facebook/sam3",
    filename="sam3.pt",
    revision="3c879f39826c281e95690f02c7821c4de09afae7",
    local_dir="/path/to/models/sam3",
)
PYTHON
```

需要 Hugging Face Mirror 时，在下载终端先设置 `export HF_ENDPOINT=https://hf-mirror.com`；模型权限要求仍然适用。已有完整本地权重可直接复用。所选 `sam3.pt` 的 SHA-256 为 `9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e`，可以用 `sha256sum /path/to/models/sam3/sam3.pt` 核对。

使用 CUDA 执行 SAM3；上游部分版本的 CPU 路径仍会创建 CUDA 张量。可选空间几何另外需要真实标定；二维运行不需要深度。

### 输入与参数

EDC 通过 `Episode.iter_frames()` 读取每条轨迹指定的初始 RGB 帧，图片和可解码视频均可作为来源；二维分布不要求固定 FPS、三视角同步或物理信号。输入应属于用户确认的同一相机／场景分组。

将以下 `checks` 合并到适用于这批数据的机器人 YAML，保留已有数据和相机映射。所有 `/path/to/...` 均为示例路径，`faceImg` 须替换为实际通道名。

```yaml
checks:
  Initial_State_Distribution:
    channel: faceImg
    checkpoint: /path/to/sam3.pt
    comparison_space: fixed_camera_same_workspace
    objects:
      - name: cup
        prompt: cup
        confidence_threshold: 0.5
    python: /path/to/sam3-env/bin/python
    device: cuda
    frame_index: 0
    grid_size: 8
```

模型环境需要 SAM3、Torch、TorchVision、Pillow 以及与调用环境完全相同的 EDC 方法源码。省略 `python` 时使用当前 Python 环境。

```bash
edc algorithms
edc run /path/to/dataset \
  --robot /path/to/robot-initial-state.yaml \
  --algorithm Initial_State_Distribution \
  --output /path/to/results/initial-state
```

默认统计输入数据集的全部轨迹；`--episode 0` 仅选第一条，用于单条验证。单条轨迹可以验证检测和输出流程，不能据此判断整批数据的覆盖充分性。同一帧内的多个目标实例也参与分布统计。

### 完整示例：一组轨迹的杯子初始分布

从源码仓库复制[初始状态配置](../../configs/robots/initial_state_example.yaml)，填写 `checkpoint`、`python` 和相机通道。选取具有可比较机位和工作区域的轨迹，在数据集根目录用 `dataset.yaml` 明确列出它们：

```yaml
schema_version: 1
id: initial_cup_example
episodes:
  - {id: episode_a, path: episode_a, format: episode}
  - {id: episode_b, path: episode_b, format: episode}
```

这是目录结构范例，`episode_a`／`episode_b` 替换为数据的稳定 ID 和相对路径。每个轨迹目录内保存原 JSON 记录与 `faceImg.mp4`；视频第 0 帧作为输入。原始数据由使用者提供，仓库只提供配置和步骤。若已有框架支持的集合元数据，直接沿用，不重写轨迹边界。

```bash
cp configs/robots/initial_state_example.yaml /path/to/robot-initial-state.yaml
# 编辑上面的配置，填写模型路径、Python 路径和场景分组，然后执行：
edc robot check --robot /path/to/robot-initial-state.yaml --data /path/to/dataset
edc run /path/to/dataset \
  --robot /path/to/robot-initial-state.yaml \
  --algorithm Initial_State_Distribution \
  --output /path/to/results/initial-state
```

打开 `report.html` 查看原生分数，通过 `catalog.json` 找到检测、热力图及统计。核对原始轨迹数、读取错误数、每类有效比例和检测框；记录 `status: completed` 且原生 `assessment_complete: true` 才表示所选二维评估完整。模型无检测时有效比例可为 0，不应把它当成读取成功且覆盖充分。

分数为 `100 × normalized_entropy × valid_episode_ratio`，**没有默认通过线**。小样本只说明所选样本的分布，不能证明整体覆盖充分或不足；机位变化也会改变图像网格位置。多个实例分别计数，`cup` 不会自动区分第一、第二只杯子。先检查检测框：例如宽泛的 `teapot` 可能同时选中茶壶和烧水壶，应明确目标提示词后重新评估，不能直接把混合类别分数用于采集决策。

### 输出与失败说明

从输出目录的 `catalog.json` 查找本次运行。`_runs/<run_id>/Initial_State_Distribution/records.jsonl` 保存 `edc_record_v1`；原生证据在该算法的 `artifacts/dataset/` 中：

| 文件 | 内容 |
|---|---|
| `initial_state_detections.json` | 逐轨迹目标框、置信度、中心位置和帧来源 |
| `distribution_summary.json` | 网格分布、熵、有效轨迹比例与原生评分 |
| `*-initial.png`、`*-probability.png` | 输入初始帧与各类别概率热力图 |
| `input-mapping.json` | 独立进程模式下的轨迹、通道与帧映射 |
| `provenance.json`、`config.json` | 模型／方法来源和生效参数 |
| `domain-errors.json`、`spatial-errors.json` | 已进入计算阶段的提取与空间计算错误 |

先查看记录中的 `status` 和错误原因：缺模型／依赖、缺通道或帧号越界属于预检诊断；解码或检测过程异常会保留已有证据并记录失败。没有检测到目标不等于读取失败，原始轨迹总数仍参与分母。预检尚未启动模型时，不保证存在模型来源文件；进入执行后，来源文件先于模型初始化写入。


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

复制[完整双臂配置示例](../../configs/robots/progress_example.yaml)，按实际数据填写后保存为 `/path/to/robot-progress.yaml`。这是输入格式范例，不是某一真实机器人的标定；不能把其中示意的确认声明当成自己的标定证据。已有机器人配置应保留原通道声明，补充相应算法参数。

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

[完整 X1 Pro 示例配置](../../configs/robots/x1pro_progress_example.yaml)使用 `faceImg`、`leftImg`、`rightImg` 三视角和 JSON 中的 `timestamp`、`follow_left/right_gripper`、`follow_left/right_joint_effort`、`follow_left/right_position`。夹爪为 cm、数值越大越张开；effort 按本例采集约定声明为 N·m，使用全部七维的 RMS 作为代理，固定变化阈值分别为 0.3／0.2 N·m。这些声明适用于本例，不能推广到所有 X1 Pro 夹爪或导出格式。

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

## 证据与失败

EDC 生成 edc_record_v1、catalog.json 和 artifacts。数据集方法通常写 artifacts/dataset/，逐轨迹位置以文件清单为准。模型初始化前保存 provenance.json：参数、源码／模型指纹、依赖和来源。初始化或后续失败保留已有证据；来源读取／写入失败不启动模型。processing_error、未完成、使用建议分离，失败不能改为通过或自动排除。

## HTML 结果报告

`edc run` 默认生成简洁的 `report.html`：算法、对象、原生分数、判定、执行状态、简短理由和详细结果链接。通过／未通过沿用算法已有判定；未配置通过阈值时显示“未设判定”，缺信息则显示“未完成”。曲线、热力图、视频和候选明细保留在原始输出中，不在 HTML 展开，也不为报告额外生成视频。`catalog.json` 指向各次运行的 HTML；`--no-html` 可关闭 HTML，JSON 与证据仍保留。分享报告时保留整个输出目录，以保持相对链接有效。

## 四事件安全检测

通过 `edc run` 执行 v62 的碰撞、洒漏、人手干预、盖子跌落四类事件检查，输出候选、裁决、完成情况和证据。人手出现在画面中不直接等于干预；原规则结合明确接触或人手引起的物体变化判断。本方法不输出通用数值分数，也不自动删除原始数据。

### 1. 安装框架和模型环境

以下命令在 EDC 仓库根目录执行。主环境需要 Python 3.10+；模型环境为 Linux x86_64、Python 3.12、可用 NVIDIA GPU。所有 `/path/to/...` 均为需替换的示例路径；模型、依赖源码和结果放在仓库外。主环境和模型环境必须安装同一份 EDC wheel。

```bash
python -m pip install build
python -m build --wheel --outdir /path/to/edc-dist
python -m pip install '/path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl[formats,value-inputs]'

git clone https://github.com/facebookresearch/sam3.git /path/to/sam3-source
git -C /path/to/sam3-source checkout 8f0b7f4d4e7eda2ed606ebde6702c93359ad01da
git clone https://github.com/facebookresearch/co-tracker.git /path/to/cotracker-source
git -C /path/to/cotracker-source checkout 82e02e8029753ad4ef13cf06be7f4fc5facdda4d
/path/to/python3.12 -m pip wheel --no-deps /path/to/sam3-source --wheel-dir /path/to/source-wheels
/path/to/python3.12 -m pip wheel --no-deps /path/to/cotracker-source --wheel-dir /path/to/source-wheels

edc env create vision --with safety \
  --python /path/to/python3.12 --venv /path/to/envs/safety \
  --edc-wheel /path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source sam3=/path/to/source-wheels/sam3-0.1.0-py3-none-any.whl \
  --source cotracker=/path/to/source-wheels/cotracker-3.0-py3-none-any.whl
edc env check --venv /path/to/envs/safety --imports
edc algorithms
```

配方固定 Torch 2.8.0、TorchVision 0.23.0、Transformers 4.57.1、NumPy 1.26.4。SAM3 和 CoTracker 从上述固定源码构建 wheel；使用实际生成的文件名。`--with safety` 使用 Transformers 本地推理，无需 vLLM。Qwen 的场景识别与裁决共用一份模型。显存和时间取决于视频长度、分辨率及候选数量；基础安装不带 GPU 依赖或权重。

### 2. 下载四个模型

使用 [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)、[SAM3](https://huggingface.co/facebook/sam3)、[CoTracker3](https://huggingface.co/facebook/cotracker3) 和 [DINOv2-small](https://huggingface.co/facebook/dinov2-small)。先在 SAM3 页面申请访问并接受条款，再登录下载；镜像不取消访问要求。这里使用原生 `sam3.pt` 和 CoTracker3 offline `scaled_offline.pth`。不要用 GRM 权重或其他模型大小代替 Qwen。

```bash
export HF_HOME=/path/to/huggingface
export EDC_MODELS=/path/to/models
## Optional mirror:
export HF_ENDPOINT=https://hf-mirror.com
/path/to/envs/safety/bin/hf auth login
/path/to/envs/safety/bin/python - <<'PYTHON'
import os
from pathlib import Path
from huggingface_hub import snapshot_download, hf_hub_download
root = Path(os.environ['EDC_MODELS'])
for repo, revision, folder in [
    ('Qwen/Qwen3-VL-8B-Instruct', '0c351dd01ed87e9c1b53cbc748cba10e6187ff3b', 'qwen3-vl'),
    ('facebook/dinov2-small', 'ed25f3a31f01632728cabb09d1542f84ab7b0056', 'dinov2-small'),
]:
    snapshot_download(repo, revision=revision, local_dir=root/folder)
for repo, revision, filename, folder in [
    ('facebook/sam3', '3c879f39826c281e95690f02c7821c4de09afae7', 'sam3.pt', 'sam3'),
    ('facebook/cotracker3', 'bf55ea50d4390e1820a267f131cd6587240fb2c5', 'scaled_offline.pth', 'cotracker3'),
]:
    hf_hub_download(repo, filename, revision=revision, local_dir=root/folder)
PYTHON
```

已有完整本地模型可直接配置其路径，无需重复下载。SAM3 文件 SHA-256 为 `9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e`，CoTracker 为 `2670d4562ed69326dda775a26e54883925cd11b6fc9b24cb7aa9f8078bce7834`。运行时 EDC 记录每个实际模型文件的指纹。

### 3. 准备输入和配置

复制[倒茶示例配置](../../configs/robots/x1pro_safety_example.yaml)及 `src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/pour_tea_task_graph.json`，先替换本地路径。示例针对 `pourtea_lzy_0720@MASTER_SLAVE_MODE@2026_07_20_10_55_07`，仅检查原始帧 **[332, 692)**。真实数据由持有者提供，仓库不附带可公开下载的数据。更换轨迹时需更新稳定 ID、范围、任务图和适用标定，不能直接把此例标定套到其他机器人。

轨迹目录需包含 `faceImg.mp4`、`leftImg.mp4`、`rightImg.mp4` 及原始 JSON。JSON 的 `data` 每行有 `timestamp`、`follow_left/right_position`、`follow_left/right_rotation`、`follow_left/right_gripper`、`follow_left/right_joint_effort`。本例明确采用采集协议的逐行对应关系，不根据三路帧数相同推断同步。

| 输入 | 要求 |
|---|---|
| RGB | EDC 原始图片或视频解码帧；`faceImg` 为参考，其他视角按显式映射或共同 clock 配对；无固定 FPS 门槛 |
| 时间 | 记录的采集／容器时间；保留播放时间身份，缺覆盖不补末帧 |
| 末端 | 米制位置、明确的姿态表示；`quaternion.order` 支持 `xyzw`、`wxyz` 或 `rotvec`（rad），统一转为内核所用 xyzw |
| 夹爪／负载 | 机器人配置声明已确认的反馈与单位；本例夹爪 cm、effort N·m，第七维沿用原方法作为末项力矩代理，不宣称已证实为夹爪力矩 |
| 几何 | 3×3 内参、每臂 4×4 臂到相机变换；与选中 RGB 对应的米／毫米深度数组 `[帧, 高, 宽]` |
| 任务图 | 实体、允许／禁止接触、四事件能力；关闭某类检查不能宣称四类全部完成 |

本例复用旧 `pour_tea_io.py` 的内参和变换、轴角解释；这些外参含旧方法的近似，不代表独立重新标定。夹爪几何计算使用米，原 v62 开合规则保留厘米数值尺度及原阈值。若字段单位、反馈身份或标定未确认，不能通过算法参数升级为已确认。

```bash
cp configs/robots/x1pro_safety_example.yaml /path/to/robot-safety.yaml
cp src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/pour_tea_task_graph.json /path/to/pour_tea_task_graph.json
```

完成数据与模型路径编辑，再准备深度。已有传感器深度可直接配置 `source: sensor_depth`、单位和对应依据；不得将单目结果标成传感器实测。

### 4. RGB 数据的可选深度准备

无传感器深度时，本例使用 [DA3NESTED-GIANT-LARGE-1.1](https://huggingface.co/depth-anything/DA3NESTED-GIANT-LARGE-1.1)。在新的环境中增加 `safety-depth` 组件，避免改动现有环境：

```bash
git clone https://github.com/ByteDance-Seed/Depth-Anything-3.git /path/to/da3-source
git -C /path/to/da3-source checkout 3d835ec1a5802d64a8b8b15f817a1ab54809bfe4
/path/to/python3.12 -m pip wheel --no-deps /path/to/da3-source --wheel-dir /path/to/source-wheels
edc env create vision --with safety --with safety-depth \
  --python /path/to/python3.12 --venv /path/to/envs/safety-depth \
  --edc-wheel /path/to/edc-dist/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source sam3=/path/to/source-wheels/sam3-0.1.0-py3-none-any.whl \
  --source cotracker=/path/to/source-wheels/cotracker-3.0-py3-none-any.whl \
  --source depth-anything-3=/path/to/source-wheels/depth_anything_3-0.0.0-py3-none-any.whl
edc env check --venv /path/to/envs/safety-depth --imports
/path/to/envs/safety-depth/bin/python - <<'PYTHON'
import os
from pathlib import Path
from huggingface_hub import snapshot_download
snapshot_download('depth-anything/DA3NESTED-GIANT-LARGE-1.1',
    revision='b2359bdf726fb44ef62acca04d629dcf158053e7',
    allow_patterns=['config.json', 'model.safetensors', 'README.md'],
    local_dir=Path(os.environ['EDC_MODELS'])/'da3')
PYTHON
```

若镜像的文件元数据请求返回 `FileMetadataError`，可直接下载同一固定修订并核对校验值。校验失败时不要启动深度准备：

```bash
mkdir -p "$EDC_MODELS/da3"
curl -fL --retry 3 https://hf-mirror.com/depth-anything/DA3NESTED-GIANT-LARGE-1.1/resolve/b2359bdf726fb44ef62acca04d629dcf158053e7/config.json -o "$EDC_MODELS/da3/config.json"
curl -fL --retry 3 https://hf-mirror.com/depth-anything/DA3NESTED-GIANT-LARGE-1.1/resolve/b2359bdf726fb44ef62acca04d629dcf158053e7/model.safetensors -o "$EDC_MODELS/da3/model.safetensors"
printf '%s  %s\n' 8ebe871a022ed58d2fc8fdfb2ebdb31d57b60fe39611c849095851a7b7c6020c "$EDC_MODELS/da3/model.safetensors" | sha256sum -c -
```

```bash
/path/to/envs/safety-depth/bin/python -m edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.prepare_depth \
  /path/to/pourtea-episode --robot /path/to/robot-safety.yaml \
  --model /path/to/models/da3 --output /path/to/depth \
  --frame-range 332 692 --depth-scale 0.9414349364434543 --device cuda:0
```

该命令直接通过 EDC 读取所选原始 RGB，为每帧估计深度并保存 RGB 校验值、原始帧号、估计内参和对应关系，不改源数据。`depth_scale` 默认为 1；此例显式复用旧方法的 `718.409 / 763.1` 缩放，仅用于复现，不能作为通用标定。单目深度对遮挡、反光或透明物体可能失准。

配置中的 `depth.episodes.<轨迹ID>.path` 指向 `depth.npy`，`correspondence_evidence` 指向生成的 `provenance.json`，`frame_range` 与本次选择一致。数组形状必须与选中原始画面逐帧、逐像素匹配；不偷偷填充缺失深度。需要全轨迹时，同时移除算法 `frame_range`，重新准备全轨迹深度并更新深度对应范围。

### 5. 运行与查看结果

```bash
## Edit all /path/to/... values in robot-safety.yaml before running.
edc robot check --robot /path/to/robot-safety.yaml --data /path/to/pourtea-episode
edc run /path/to/pourtea-episode \
  --robot /path/to/robot-safety.yaml \
  --algorithm Collision_Detection_Qwen3vl \
  --output /path/to/new-safety-results
```

`python` 参数决定独立模型环境，无需手工激活它。使用 `safety-depth` 环境时把配置中的 `python` 改为该环境路径。输出必须是原数据之外的新目录。

打开输出根目录 `report.html` 查看简洁的通过／未通过／需复核、理由和完整结果入口。通过表示所选范围完成检查且无确认或待复核事件，不是安全认证；不伪造数值分数。`catalog.json` 索引所有记录及证据。

完整执行应同时满足：`records.jsonl` 的 `status: completed`、`native-result.json` 的 `assessment_complete: true`、`checks` 下四类均为 `completed`。确认事件产生“未通过”，待复核事件产生“需复核”；这不等于程序运行失败。检查 `assessment_scope.original_frame_range`，示例完成不代表整条轨迹已检查。

| 证据 | 内容 |
|---|---|
| `input-mapping.json`、`physical-mapping.json` | 原始帧、时间、视角配对、信号单位和转换 |
| `input/video-mapping.json` | 内部 30 FPS 展示视频到原始帧的映射；30 FPS 是编码参数 |
| `provenance.json`、`worker.log` | 模型／源码／配置指纹、运行诊断 |
| `native/safety_report.json` | 四事件原生汇总和扫描覆盖 |
| `native/events_adjudicated.json` | 候选、确认／拒绝／需复核裁决与依据 |
| `native/model-requests/`、`native/collision/` | 模型输入图、原始响应、分割、跟踪和几何证据 |
| `native-result.json` | 完成情况、简洁判定和实际评估范围 |

证据位于 `_runs/<run_id>/Collision_Detection_Qwen3vl/artifacts/<episode-key>/`。完整复核视频默认不导出；内部扫描视频、帧缓存和几何输入用于执行及追溯，不应把它们当原始视频。缺输入／依赖、推理失败或检查未完成会保留已有证据；命令退出成功、生成 HTML 或没有事件文件，都不能单独证明完成。

### 来源与使用边界

实现位于 `src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/`，由标准 `AlgorithmSpec + run(context, params)` 注册，以 `edc_record_v1` 返回，不依赖研究仓库或 DQS。候选提取与裁决保留冻结 v62 方法，真实准确率需要按任务另做评估。

`SOURCE.json` 记录原文件校验值及尚未核实的源码许可。模型和依赖分别遵循各自条款；CoTracker 和此 DA3 模型含非商业限制，SAM3 使用其单独条款。模型、原始数据、历史结果和内部验收记录均不进入 EDC 安装包。
