# Collision_Detection_Qwen3vl

[English](README.md)

通过 `edc run` 执行 v62 的碰撞、洒漏、人手干预、盖子跌落四类事件检查，输出候选、裁决、完成情况和证据。人手出现在画面中不直接等于干预；原规则结合明确接触或人手引起的物体变化判断。本方法不输出通用数值分数，也不自动删除原始数据。

## 1. 安装框架和模型环境

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

## 2. 下载四个模型

使用 [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)、[SAM3](https://huggingface.co/facebook/sam3)、[CoTracker3](https://huggingface.co/facebook/cotracker3) 和 [DINOv2-small](https://huggingface.co/facebook/dinov2-small)。先在 SAM3 页面申请访问并接受条款，再登录下载；镜像不取消访问要求。这里使用原生 `sam3.pt` 和 CoTracker3 offline `scaled_offline.pth`。不要用 GRM 权重或其他模型大小代替 Qwen。

```bash
export HF_HOME=/path/to/huggingface
export EDC_MODELS=/path/to/models
# Optional mirror:
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

## 3. 准备输入和配置

复制[倒茶示例配置](../../../../../../configs/robots/x1pro_safety_example.yaml)及本目录的 `pour_tea_task_graph.json`，先替换本地路径。示例针对 `pourtea_lzy_0720@MASTER_SLAVE_MODE@2026_07_20_10_55_07`，仅检查原始帧 **[332, 692)**。真实数据由持有者提供，仓库不附带可公开下载的数据。更换轨迹时需更新稳定 ID、范围、任务图和适用标定，不能直接把此例标定套到其他机器人。

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

## 4. RGB 数据的可选深度准备

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

## 5. 运行与查看结果

```bash
# Edit all /path/to/... values in robot-safety.yaml before running.
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

## 来源与使用边界

实现位于 `src/edc/algorithms/value/algorithms/Collision_Detection_Qwen3vl/`，由标准 `AlgorithmSpec + run(context, params)` 注册，以 `edc_record_v1` 返回，不依赖研究仓库或 DQS。候选提取与裁决保留冻结 v62 方法，真实准确率需要按任务另做评估。

`SOURCE.json` 记录原文件校验值及尚未核实的源码许可。模型和依赖分别遵循各自条款；CoTracker 和此 DA3 模型含非商业限制，SAM3 使用其单独条款。模型、原始数据、历史结果和内部验收记录均不进入 EDC 安装包。
