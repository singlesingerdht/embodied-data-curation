# LeRobot 数据存储与检测包装

## 安装和边界

基础安装仍只有 NumPy/PyYAML；官方读取与转换使用 Python 3.12 以上的可选安装：

~~~bash
pip install 'embodied-data-curation[lerobot]'
~~~

首轮固定软件版本 LeRobot 0.6.1，目标数据格式 v3.0。v3 默认使用官方包装；v2.1 默认兼容读取。
显式选择 --backend official 或 --backend compat，不会因缺依赖自动偷偷换后端。
官方包装使用本地 LeRobotDataset 的 HF 数据视图 with_format(None)，按元数据还原 dtype；
不使用仍带 torch 转换的 get_raw_item，以免 float64 降为 float32。没有修改第三方方法。

## 转换和读取

~~~bash
edc convert-to-lerobot /path/to/episode.hdf5   --profile /path/to/robot.yaml --fps 30 --output /new/path/luoshi-v3
edc convert-to-lerobot /path/to/lerobot-v21   --profile /path/to/robot.yaml --output /new/path/dexhand-v3
edc inspect /new/path/luoshi-v3 --backend official
edc run /new/path/luoshi-v3 --output /new/path/detection   --params /path/to/algorithm-params.json
~~~

~~~python
from edc import convert_to_lerobot, open_dataset
result = convert_to_lerobot(source, profile=profile, output=new_directory, fps=30)
with open_dataset(new_directory, backend="official") as dataset:
    with next(dataset.iter_episodes()).opened() as episode:
        raw = episode.read_signal("observation/state/arm/position", "position")
~~~

第一轮转换限 HDF5 和 LeRobot v2.1，源文件只读，目标必须全新且与源路径互不包含。
HDF5 必须提供 timestamp_column 及显式名义 FPS；按 metadata/episode_group 划分轨迹。
不同采样数、损坏数据、无法解释的类型明确失败。失败副本只作诊断，open_dataset 拒绝执行检测。
已有 HDF5/MCAP 读取仍保留。未新增训练策略、数据清洗、自动补值或有损降精度。

## 补充配置复用

转换后 robot.yaml 复用 channels、parts、select、单位和算法参数，在 lerobot 中补充特征位置：

~~~yaml
schema_version: 1
id: example_robot
version: '1'
channels:
  arm_angle:
    modality: joint_space
    fields: [position]
    unit: deg
    source_kind: feedback
    calibration_status: confirmed
    representation: {quantity: angle, semantic_status: confirmed}
lerobot:
  feature_mapping:
    arm_angle:
      feature: observation.arm_angle
      field: position
      source_sample_shape: [7]
  original_time:
    feature: source.raw_time
    unit: unknown
    time_source: unknown
    clock: null
~~~

feature_mapping 只登记存储位置、字段和源样本形状；同一单位/关节信息不再填两份。
多分量左右侧或混合单位仍通过 channels.<新通道>.select 指定索引，禁止右侧缺失回退左侧。
官方后端物理检查要求 quantity 非未知且 semantic_status: confirmed；缺省不等于确认。
仅填写 deg 不会改变存储值：90 保留为 90，执行时由既有 SI 视图转换为 π/2，记录换算证据。
manual 单位策略关闭自动换算，此时非 SI 输入被拒绝。基础有限值检查仍可检查原值。

LeRobot timestamp 为索引时间，HDF5 的原始时间单独保存。示例名义 30 FPS 不证明采集频率。
时间单位未知仍保留原值；采集间隔、同步不获得可用条件。独立时钟不得靠同一索引强行对齐。

## 图像与来源

- 珞石 JPEG 解码成 RGB 后以 PNG 嵌入标准图像列，每帧像素一致；原 JPEG 字节哈希和源定位另存。
- zlib 深度恢复为 uint16 定形数值特征，包装声明 depth；没有套用官方深度视频的量化或单位推断。
- v2.1 视频逐字节复制进 v3 视频布局，逐帧计数和区间检查；完整单轨视频直接用于原编码检查。
- 派生预览检查标记 inspection_object: derived_preview，不能宣称检查了原 JPEG 或原视频编码。
- 每个 HDF5 字段均进清单；空数组/显式事件以原 dtype 的 NPY 证据保留，不伪造逐帧值。
  其他非逐帧字段需 lerobot.non_frame_fields 显式登记，不静默丢弃。

## 验收与报告

catalog.json 的 conversions 索引 _runs/<run_id>/consistency.json、run.json、源映射、错误与逐帧哈希。
acceptance.json 分开记录 conversion_status、fidelity_status、training_status、detection_status、quality_status。
转换阶段不生成质量通过结论。检测通过现有 edc run 生成固定 dqs_report_v2 HTML。
训练侧验收官方 LeRobotDataset 和 DataLoader 每条轨迹首/中/末帧，保真侧验证所有元素及所有图像帧。

统计归一化参数本轮明确留空：未经清洗的 NaN/Inf 不适合官方分位数统计，不为使统计通过而修改数据。
官方数据读取与 DataLoader 可用，不表示具体策略训练及归一化配置已经验收。
原始数据、第三方和算法许可仍分别核查；本地通过不意味着真实数据已经获得公开分发许可。


当前合成回读／官方接口测试及版本见 [STATUS](STATUS.md)。真实样例与历史转换验收脚本保留在原研究仓库。
