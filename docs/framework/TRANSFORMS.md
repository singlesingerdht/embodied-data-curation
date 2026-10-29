# 显式机器人变换与相机参数

这些 API 只使用输入参数，不估计标定、不猜单位、不覆盖原始数据。内部实现位于
`src/edc/data/transforms`，对外由 `edc.data.transforms` 导出。基础安装仅用 NumPy/PyYAML。
完整合成配置见 `examples/onboarding/transforms.yaml`；真实机器人必须替换示例参数。

## 配置与调用

`robot.yaml` 保留 schema_version: 1，在 `transforms` 下添加 `clocks`、`frames`、
`chains`、`cameras`。各部分可独立配置。加载 profile 时校验矩阵、单位、关节轴及相机模型。
`robot check` 还检查相机 channel 是否存在且可解码；不会证明所填标定在物理上正确。
每个定义可附 `provenance`、日期或供应商版本等来源记录，变换结果会保留使用的定义。

```python
from edc import open_dataset
from edc.data.transforms import RobotTransforms

with open_dataset('/path/to/data', profile='/path/to/robot.yaml') as dataset:
    transforms = RobotTransforms(dataset.profile)
    for episode in dataset.iter_episodes():
        with episode.opened():
            angles = episode.transform_signal('/joint_states', 'position', target_unit='rad')
            times = episode.transform_timestamps('/joint_states', 'host_clock')
            tool = episode.forward_kinematics('arm')
            pixels = transforms.project('head', tool.output[..., :3, 3], source_frame='base')
            # 每个结果都有 output、params、source_mapping。
            # 插件中用 context.write_result('fk.json', tool.describe()) 留存证据。
```

上述 API 在直接读取源 Episode 时按需调用，原始 read_signal/read_values 保留源单位。
公共执行入口 edc.run / edc run 默认创建算法专用 SI 视图，所有内置适配器和
插件的 context.episode、context.dataset.iter_episodes()、algorithm_view() 均通过该视图取数。
它根据 channel.unit / field_metadata.<field>.unit 调用已有 convert_units；无需另配 transforms。
源 profile 保留源单位，算法应从执行视图的 channel.field_spec() 获取执行单位，不能用源 profile
的 unit 解释执行数组。历史 standalone 算法入口不经过新执行器，行为保持兼容。
clock 变换不进行重采样；FK 不插值。投影结果的 source_mapping 保留输入行对应关系，
若输入来自 FK，保存证据时应同时保留 FK 的 source_mapping，组成完整变换链。
`describe()` 将数组转换为列表；非有限值结果应通过公共结果写入器保存。

## 单位

```python
from edc.data.transforms import convert_units
result = convert_units([0, 90, 180], 'deg', 'rad')
```

支持 m/cm/mm、rad/deg、s/ms/us/ns、m/s/mm/s、m/s²/mm/s²（写作 `m/s^2`）、
rad/s/deg/s、rad/s²/deg/s²、A/mA、N、N*m/N*mm、无量纲 `1`。
只允许同物理维度的比例换算，不支持电流→力矩、编码器 tick→角度等需要额外模型的转换。
数值中的 NaN/Inf 保持为异常值，不自动填补。几何运算和 FK 则拒绝非有限输入。

### 算法执行策略

单位也可以暂留 null、空字符串或 unknown；物理量未确认时保持 unknown，不按字段名猜测。
单位未知的原始值可做 NaN/Inf 等诊断，不能进入物理算法。SI 不等于算法适用：头部伸缩的 m
不能代替角度算法需要的 rad；混合数组须由用户确认索引后分别换算。推荐池与配置例子见
[UNITS_AND_ALGORITHM_POOL.md](UNITS_AND_ALGORITHM_POOL.md)。


```bash
edc run /path/to/data --profile robot.yaml --output /path/to/results
# 由用户自行转换：关闭自动换算，要求数据已为 SI，并如实声明转换后数据的单位。
edc run /path/to/si-data --profile robot.si.yaml --output /path/to/manual-results --unit-policy manual
```

Python 对应 run(dataset, unit_policy="auto") / run(dataset, unit_policy="manual")；
inspect 也支持同名 CLI 参数，用于预检策略，不代表已读取并换算载荷。
manual 不会把 deg 原值透传到物理算法；非 SI/未知输入被阻止。
未知/不支持单位不猜测，依赖该输入的算法保留 missing_input / needs_calibration；
Numeric_Check 仍可专门检查原始 NaN/Inf，但不套物理阈值，也不标记完整评估。
不依赖物理单位的结构、时间相等比较和视频检查可继续执行。

例如源关节位置 [0,90,180] 与 unit: deg 一起保留；算法读取到 [0,pi/2,pi]，
执行元数据为 rad。阈值始终用 SI，不因源数据以 deg 表示而重缩放。
Numeric_Check 的配置确认仍为必需步骤，可确认可换算的源单位，不自动批准配置；算法作者标识与其他内置 CPU 算法一致。
Reset_Detection 中明确声明单位的 reference/tolerance 同时换算为 SI；manual 下也须自行准备 SI 标定值。
原始文件、单位声明、clock/time_source 与样本顺序保持不变；读取器已为秒的时间轴不再次缩放，
秒制不代表采集同步。

run.json 保存策略；每算法的 unit-normalization.json 保存源/目标单位、因子、源映射、
读取次数和 conversion_applied。conversion_required 只是计划，只有实际 SI 读取才标记已换算。
未读取字段不能作为执行证据。执行专用视图的 inspect_recorded_signal 仅供有限值/等值诊断；
物理算法应使用 read_signal/read_values，不得通过原始出口绕过 SI 约束。

## 时钟

所有读取格式及多模态边界见 [读取、单位与时间](READING_UNITS_AND_TIME.md)。
HDF5／Parquet／轨迹 JSON 的原时间单位未填、null、空串或 unknown，均保持未知；
保留精确原值供时间相等检查，不默认秒，也不启用采集间隔／跨路同步。
LeRobot 标准索引时间和 FPS 生成时间保持其索引／合成身份。
下面的显式时钟工具不代表 CPU 时间算法已经自动使用映射；Sync_Check／Root_Cause 仍需
输入本身具备共同 clock，不能仅填写 transforms.clocks 就当成映射已执行。

```yaml
transforms:
  clocks:
    robot_to_host:
      source: robot_clock
      target: host_clock
      scale: 1.00001
      offset_seconds: 0.25
      valid_range: [0, 3600]
      provenance: supplied_clock_measurement_v1
```

公式：`target_seconds = source_seconds * scale + offset_seconds`。IR 时间已统一为秒，
不要在这里再次把纳秒乘成秒。scale 必须为正；valid_range 是源时钟秒值的有效区间，
正向和逆向都禁止超出该区间。未提供区间意味着调用方声明映射对全部输入有效。
支持一条显式映射及其逆变换，暂不自动串接时钟映射或拟合漂移。
变换保留 acquisition/container/playback/synthetic/unknown，不构成采集同步验收。
大绝对时间仍受 float64 精度约束，高精度同步应另行建立相对时间基准。

## 坐标

```yaml
transforms:
  frames:
    base_to_camera:
      source: base
      target: camera_optical
      translation_unit: m
      matrix: [[1,0,0,0], [0,1,0,0], [0,0,1,2], [0,0,0,1]]
```

采用列向量：`p_target = T_target_source @ p_source`，matrix 必须为 4×4 刚体矩阵，
旋转正交且行列式为 +1。translation_unit 只作用于平移项。
静态 frame 图允许组合与求逆；拒绝环和重复边，避免多条标定路径产生歧义。

`RobotTransforms.coordinates(values, source, target, kind=..., unit=...)`：

- `points`：输入 (...,3)，换成米后进行旋转和平移，输出米。
- `vectors`：输入 (...,3)，只旋转，保留输入单位；不适用于六维 wrench/twist 的伴随变换。
- `poses`：输入 (...,4,4)，转换平移单位并左乘外参，输出平移为米的刚体矩阵。

`episode.transform_signal(..., target_frame='base', kind='points')` 从字段元数据获取输入坐标系和单位。
若同时指定 target_unit 与 target_frame，点/位姿最终坐标输出仍统一为米，以结果 params 为准。
四元数、欧拉角、六维旋转输入暂不自动推断，调用方应先明确表示并转换。

## FK

`chains` 是有序串联关节链。每关节必填 name、type、origin、translation_unit。
origin 将关节零位坐标映射至前一 link；轴在关节局部坐标系表达。
计算顺序：依次乘 `origin @ motion(q)`，最终得到 `T_base_tip`。

- fixed：只有 origin。
- revolute：单位轴 axis、角度单位 unit（rad/deg）、可选 limits。
- prismatic：单位轴 axis、长度单位 unit（m/cm/mm）、可选 limits。

limits 与输入 q 使用同一声明单位；越界报错，不裁剪。
末端工具偏置可作为最后一个 fixed 关节。

直接调用 `RobotTransforms.fk('arm', {'shoulder': [...], ...})`，字典必须精确包含全部移动关节；
每个关节的标量/批次数组形状必须一致，不隐式广播。

Episode 调用额外使用每个移动关节的绑定：

```yaml
input: {channel: /joint_states, field: position, index: 0}
```

读取原字段，再按字段单位转换为链声明单位。多个输入必须拥有完全相同的时间轴、时钟、
时间来源和指令/反馈性质，否则拒绝计算。不会用“行数一样”替代时间对齐。
输出 source_mapping 保存各关节的 topic、字段、索引、原单位及时间轴。
URDF 导入见下文；本版不支持 mimic/闭链、关节标零估计、IK 或机器人专属 DH 约定。

## 相机内外参

```yaml
transforms:
  cameras:
    head:
      channel: /head/image
      frame: camera_optical
      image_size: [640, 480]       # width,height，参数对应的图像尺寸
      K: [[500,0,320], [0,500,240], [0,0,1]]
      distortion_model: plumb_bob
      distortion: [0,0,0,0,0]    # k1,k2,p1,p2,k3
      provenance: user_supplied_calibration
```

外参统一放 frames，通过 camera.frame 连到机器人坐标系。
光学坐标约定：x 向右、y 向下、z 向前，像素坐标为 (u,v)。
首版支持零 skew pinhole，distortion_model 为 `none`（空参数）或 `plumb_bob`（5 参数）。
鱼眼、rational polynomial、双目 rectification 暂不支持，配置时明确失败。

投影使用所填内参、畸变及外参，深度非正时报错；图像外像素保持原值，不裁剪、不视为可见。
图像缩放或裁剪后应输入对应的新 K/image_size，框架不自动补偿。
移动相机可由 FK 产生逐帧位姿，但尚未自动注入静态 frame 图；调用方需显式求逆并逐帧组合。
所有标定数值由用户提供，我们仅校验格式和数学约束，不提供标定结果。

## URDF 导入与 FK（已实现）

用户提供**展开后的 URDF**；框架只读取 link/joint，不加载 mesh，不运行 xacro，不进行标定。
支持 fixed、revolute、continuous、prismatic。URDF 的 xyz 使用米、rpy 使用弧度，
旋转为 Rz(yaw) @ Ry(pitch) @ Rx(roll)，轴在关节局部坐标内；输出为 T_base_tip。
所选链含 mimic、floating/planar 时明确失败；闭环、多父节点及错误引用拒绝。
不受选定链影响的其它分支 mimic 不参与计算。

```yaml
transforms:
  chains:
    left_arm:
      urdf: ../models/robot.urdf  # 相对当前 robot.yaml；dict 配置则相对进程工作目录
      base_frame: base_link
      tip_frame: left_tool
      inputs:                   # 用 Episode FK 时逐一绑定，名称来自 URDF
        left_joint_1: {channel: left_arm_position, field: position, index: 0}
        # 继续声明该链其余所有活动关节；不允许猜测关节顺序
```

加载 profile 时展开为既有链配置，保留 URDF 路径和 SHA-256；run.json 保存展开后的几何快照，
后续 URDF 文件改变不影响已经加载的配置。重新加载配置才使用新模型。

```python
from edc.data.transforms import import_urdf_chain, RobotTransforms
chain = import_urdf_chain('/path/to/robot.urdf', 'base_link', 'left_tool')
transforms = RobotTransforms({'transforms': {'chains': {'left_arm': chain}}})
# 每个活动关节必须有对应值；支持标量或相同长度的批量样本。这里的名称仅是示意。
result = transforms.fk('left_arm', joint_positions_by_urdf_name)
# 已绑定输入的真实数据：result = episode.forward_kinematics('left_arm')
```

单独调用 `import_urdf_chain` 返回可序列化链定义；FK 返回 output/params/source_mapping。
真实模型仍需核对机型、关节顺序、角度单位、零位、末端 link 和参考实测位姿。

## 可选重采样与时间对齐（默认关闭）

读取、检查、执行算法都不会自动重采样。开发者按需调用：

```python
from edc.data.transforms import resample
r = resample([[0.0], [2.0], [4.0]], [0.0, 1.0, 2.0], [0.5, 1.5],
             method='linear', max_gap=1.0)
# r.output == [[1.0], [3.0]]；时间以秒计，调用者必须确保处于同一时钟。

# 将关节值匹配到相机时间轴；源/参考必须有共同 clock 或手动配置的直接时钟映射。
r = episode.align_signal('left_arm_position', 'position', 'head_rgb',
                         method='nearest', max_gap=0.05)
```

- nearest 选最近帧，等距选较早帧；max_gap 限制与所选源帧的时间差。
- linear 逐分量插值；max_gap 限制左右源帧的间隔。它不适用于四元数/旋转矩阵或周期角度的跨周期插值。
- 不外推，不自动裁剪参考时间轴，不修改原始通道。重复、倒序、NaN 时间戳明确拒绝。
- max_gap 不填则只执行边界检查；使用者应按任务设置可接受间隔。
- 保留原时间、映射后时间、参考通道、源行索引/插值权重、原单位与指令/反馈语义。
- synthetic/playback/unknown 来源不升级为 acquisition；对齐成功不证明采集同步。
- 重采样结果由算法主动消费；不会替换旧算法的默认输入，也不隐式开启 Episode FK 跨通道插值。
