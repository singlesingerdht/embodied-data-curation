# Initial_State_Distribution

[English](README.md)

通过 EDC `AlgorithmSpec + run(context, params)` 调用。随包 `SOURCE.json` 保留来源校验值和未核实的许可状态；本说明不宣称代码复用权、模型或数据许可已经核实。

## 通过 edc 调用

本说明适用于 `edc algorithms` 已列出 `Initial_State_Distribution` 的安装。算法清单表示入口已注册；实际执行还需要可读取的数据、SAM3 依赖和本地权重。基础安装不附带模型，也不自动下载权重。

### 安装

在框架源码目录执行 `python -m pip install '.[formats,initial-state]'`，安装读取依赖与初始帧缓存需要的 Pillow。模型环境独立准备；基础安装不会引入 Torch、SAM3 或模型权重。

可采用 Python 3.12、Torch 2.8.0、TorchVision 0.23.0、Transformers 4.57.1、Tokenizers 0.22.1、NumPy 1.26.4 的依赖组合。SAM3 源码包和权重需另行取得；下面的 wheel 路径为占位路径，EDC wheel 须与调用环境的方法源码一致。

```bash
python3.12 -m venv /path/to/sam3-env
/path/to/sam3-env/bin/python -m pip install \
  /path/to/embodied_data_curation.whl /path/to/sam3.whl \
  'torch==2.8.0' 'torchvision==0.23.0' \
  'transformers==4.57.1' 'tokenizers==0.22.1' 'numpy==1.26.4' \
  'Pillow>=11,<12' 'opencv-python-headless==4.11.0.86' \
  'timm>=1.0.17,<2' 'ftfy==6.1.1' 'einops>=0.8,<1' \
  'pycocotools>=2,<3' 'psutil>=5' 'setuptools>=70,<81'
```

使用 CUDA 执行 SAM3；`device: cpu` 是否可用取决于上游 SAM3 版本，部分版本仍会初始化 CUDA 张量。可选空间几何另需 OpenCV 和真实标定，其合成测试不能证明某台相机的标定或单目尺度准确。

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
