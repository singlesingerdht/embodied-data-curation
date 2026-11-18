# Initial_State_Distribution

[English](README.md)

通过 EDC `AlgorithmSpec + run(context, params)` 调用。随包 `SOURCE.json` 保留来源校验值和未核实的许可状态；本说明不宣称代码复用权、模型或数据许可已经核实。

## 通过 edc 调用

本说明适用于 `edc algorithms` 已列出 `Initial_State_Distribution` 的安装。算法清单表示入口已注册；实际执行还需要可读取的数据、SAM3 依赖和本地权重。基础安装不附带模型，也不自动下载权重。

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

从源码仓库复制[初始状态配置](../../../../../../configs/robots/initial_state_example.yaml)，填写 `checkpoint`、`python` 和相机通道。选取具有可比较机位和工作区域的轨迹，在数据集根目录用 `dataset.yaml` 明确列出它们：

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


模型初始化前保存 provenance.json，失败保留已有证据。独立进程环境需安装完全相同的方法源码。

可选参数 `python` 指定独立 SAM3 环境的解释器，`timeout_seconds` 限定子进程时长；未指定 `python` 时保留当前进程执行。EDC 准备初始 RGB 图片与样本映射，两种入口共用检测和统计方法。

## HTML 结果报告

`edc run` 默认生成简洁的 `report.html`：算法、对象、原生分数、判定、执行状态、简短理由和详细结果链接。通过／未通过沿用算法已有判定；未配置通过阈值时显示“未设判定”，缺信息则显示“未完成”。曲线、热力图、视频和候选明细保留在原始输出中，不在 HTML 展开，也不为报告额外生成视频。`catalog.json` 指向各次运行的 HTML；`--no-html` 可关闭 HTML，JSON 与证据仍保留。分享报告时保留整个输出目录，以保持相对链接有效。
