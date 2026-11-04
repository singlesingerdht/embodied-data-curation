# 快速开始

[返回主页](../README.zh-CN.md) · [English](QUICKSTART.md)

本例使用一份 **X1 Pro 双臂机器人真实 MCAP**，完成“准备数据 → 推荐与选择 → 运行 → 查看报告”。配置与原始数据分开，以下 `/path/to/...` 均替换为本机路径。

## 1. 安装环境

```bash
cd /path/to/embodied-data-curation
python -m venv .venv
source .venv/bin/activate
pip install '.[formats]'
```

Python ≥3.10。基础安装 `pip install .` 仅提供轻量核心，读取格式与执行全部 CPU 方法按需安装 `formats`。官方 LeRobot 后端另需 Python ≥3.12 和 `pip install '.[lerobot]'`。视频容器检查还需环境中可用的 `ffmpeg`／`ffprobe`。

<a id="data"></a>
## 2. 准备数据

当前示例已在本地保存真实数据副本，**MCAP 不随仓库分发，尚无公开下载入口**。拿到同一份录制后放到如下路径；其他数据须先核对字段和配置。

```bash
mkdir -p /path/to/dataset
cp /path/to/source/episode.mcap /path/to/dataset/episode.mcap
python examples/quickstart/list_inputs.py /path/to/dataset/episode.mcap
```

| 内容 | 本例 |
|---|---|
| 机器人／输入 | X1 Pro，双臂各6个关节；单次 MCAP 录制 |
| 大小／轨迹时长 | 663,706,769 bytes；容器跨度约48.28秒 |
| 相机 | 头部、胸前、左右腕部四路 RGB；另有头部深度 |
| 数值 | 双臂关节与末端反馈、主端指令及其他已登记字段 |
| 配置 | [`configs/robots/x1pro_mcap.yaml`](../configs/robots/x1pro_mcap.yaml) |
| 源文件 SHA-256 | `95993aab130d1ffa2ea9cb9367911f8216f90b2268b076136908f7130053b6d2` |

关节位置rad、角速度rad/s及末端XYZ的m声明沿用已确认信息；未知物理量、部分阈值确认及共同clock继续保留未知。该配置是完整 profile，用于这份 MCAP；[`x1pro.yaml`](../configs/robots/x1pro.yaml) 对应另一种 JSON＋MP4 布局，不能互换使用。单文件轨迹边界来自数据提供者的既有说明，不通过文件数量推断。

该文件用于本地示例验证；可分发的真实样例仍需确认数据许可。复制操作保持原始文件内容不变。

## 3. 推荐与运行

可先查看每项算法是否具备输入条件：

```bash
python examples/quickstart/recommend.py /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml
```

首次查看完整九项质量集合，生成 HTML：

```bash
edc batch /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --output /path/to/results/report.html \
  --keep /path/to/results/details --jobs 1
```

`--keep` 可省略；保留时使用尚不存在的目录。只选部分算法改用 `edc run --algorithm ...`，见[算法选择](ALGORITHMS.zh-CN.md#selection)。不需要修改算法代码。

<a id="results"></a>
## 4. 查看结果

用浏览器打开 `/path/to/results/report.html`。`report.json` 是汇总结论，`details` 保存各算法完整输出；`catalog.json` 定位运行记录与证据。输出目录须与原始数据分开。

这份 MCAP 的参考汇总结论为**建议复核**。以下结果对应本例配置，不表示整机健康认证或其他采集批次的结论：

| 算法 | 结果 | 简要说明 |
|---|---|---|
| Structure_Check | 通过 | 结构可读取；四路 RGB 缺少配对 camera_info 的提示保留 |
| Numeric_Check | 复核 | 已读取字段的有限性可检查；物理参数确认不完整，未当作物理检查全部通过 |
| Action_Smoothness | 通过 | 左右臂保持段分别评估；运动段指标仅参考 |
| Check_Timestamp | 复核 | 可检查逐路间隔，跨路条件不完整 |
| Sync_Check | 不适用 | 共同 clock 未确认，不能据此证明采集同步 |
| Root_Cause | 不适用 | 缺跨路时间比较条件 |
| Dedup_Check | 复核 | 部分原始通道缺有效时间，范围未完整覆盖 |
| Reset_Detection | 复核 | 按已有 Home 标定产生复核结果；当前短摘要未展开具体偏差，需查看保留输出 |
| Video_Integrity | 通过 | 图片抽检；左右腕部各290帧中分别3帧、2帧模糊 |

推荐、执行成功与质量通过是三个不同概念。完整记录见本地生成的报告；文档只展示必要摘要。[结果阅读与定位](ALGORITHMS.zh-CN.md#results)
