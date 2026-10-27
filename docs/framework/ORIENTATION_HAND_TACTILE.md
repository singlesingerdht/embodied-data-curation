# 姿态表示、手部、夹爪与触觉通道

## 中间层不止预览中的 8 项

完整标准词表有 107 个逻辑名称、21 类模态，见 [CHANNEL_INVENTORY](CHANNEL_INVENTORY.md)。
上次预览的 8 项只是部分真实通道及配置声明，既不是完整词表，也不是 8 路独立源 topic。
源 topic/feature、逻辑名称、机器人部件和字段表示是独立配置。

| 类型 | 现有逻辑名 | 中间层表示 | 本轮验证边界 |
|---|---|---|---|
| 灵巧手关节 | `joint/hand/{left,right,main}/{state,feedback,command}`（9 项） | `modality: joint_space`，`device_type: dexterous_hand`，字段及 DOF 按设备登记 | 合成 JSON/HDF5/Parquet 的左右指令与反馈进入 Numeric_Check；无真实灵巧手样例验收 |
| 夹爪 | `joint/gripper/{left,right,main}/{state,feedback,command}`（9 项） | `modality: joint_space`，`device_type: gripper`；关节角、开口距离、电流及力不能互相代替 | X1 有左右夹爪状态及指令；新增四配置接入示例与跨格式合成验证 |
| 触觉 | `tactile/hand/{left,right,main}`（3 项） | `modality: tactile`，显式字段和张量维度；quantity/unit/layout 保持实际含义 | 保留 N×H×W 阵列、逐行映射，可做通用有限值检查；未新增滑移、接触重建、坏点物理算法 |
| 接触状态 | `contact/hand/{left,right}` 等 | `modality: contact`，明确传感器/派生来源 | 名称和字段声明不等于已实现所有设备协议 |

多传感器实例使用 `@`，例如 `tactile/hand/right@palm`、`tactile/hand/right@index_tip`。
构型部件由 `parts` 定义；同一机器人可以只有一只夹爪，也可以双手、多指或混合配置。
源设备自定义 ROS 消息仍需要相应 reader/decoder，不能靠写一个逻辑名自动解码。

新入口是 `configs/hand_gripper_tactile.yaml`，引用机器人、数据集、算法三个子配置和质量阶段。
模板包含左右灵巧手、夹爪及掌心/指尖触觉的可选声明；删除实际不存在的设备，再绑定真实源。
12 个示例声明使用既有标准名和实例后缀，没有虚增标准词表数量。
`source_bindings` 中的 `null` 值阻止执行；未填 DOF、单位、标定或触觉阵列尺寸。

## 中间层 orientation 统一为 XYZW 四元数（0.2.0a2）

公共 `open_dataset`、`open_config_dataset`、`open_dataset_reference` 和算法执行入口
默认将 `read_signal(..., 'orientation')`、`read_values` 及算法桥的姿态统一为
**N×4、XYZW、无量纲单位 `1` 的单位四元数**。源格式仍可使用下表任一明确声明。
自动转换仅在读取时执行；元数据目录中的转换计划不表示数值已经验证。
字段级 `field_metadata.orientation.representation` 优先于通道级 `representation`。

| kind | 必填表示参数 | 单条形状 |
|---|---|---|
| `quaternion` | `order: xyzw` 或 `wxyz` | `[4]` |
| `rpy` | `axes: xyz`、`convention: extrinsic`、`unit: rad` 或 `deg` | `[3]` |
| `rotation_matrix` | `layout: matrix` | `[3,3]` |
| `rotation_matrix` | `layout: row_major` 或 `column_major` | `[9]` |

RPY 此入口明确定义为右手固定 XYZ 轴，列向量约定，
`R = Rz(yaw) Ry(pitch) Rx(roll)`；其他欧拉旋转顺序必须先显式适配，不能直接标成这里的 RPY。
四元数分量重排也显式记录；矩阵不会因长度恰好为 9 就自动认定为旋转矩阵。
未知、非有限、非单位四元数、非正交或行列式非 +1 矩阵拒绝转换，不静默修复或丢行。
`inspect_recorded_signal` 仍保留这些异常以供质量检查，普通读取不会退回非统一数据。

例如数据源的角度为度制 RPY，可这样声明（示例，不表示真实设备确认）：

```yaml
channels:
  raw_attitude:
    logical_name: pose/tracker/main
    modality: pose
    fields: [orientation]
    field_metadata:
      orientation:
        dimensions: [3]
        unit: deg
        representation: {kind: rpy, axes: xyz, convention: extrinsic, unit: deg}
```

算法使用现有注册接口，普通读取已经得到统一结果：

```python
quaternions = context.episode.read_signal('pose/tracker/main', 'orientation')
# 源 [roll=0°, pitch=0°, yaw=90°] -> [x=0, y=0, z≈0.7071, w≈0.7071]
evidence = context.episode.orientation_evidence()
```

每个运行结果保存 `orientation-normalization.json`，区分声明、实际读取、失败原因及源行映射。
`unit_policy: manual` 只控制其他物理量的 SI 换算，不关闭姿态统一。

需要审计源异常时，显式调用 `episode.inspect_recorded_signal(channel, 'orientation')`，
源元数据通过 `episode.recorded_channels` 访问。`Numeric_Check` 的姿态原值有限性分支
使用这一入口，保留 NaN/Inf 的原始分量位置，不把该分支计为物理姿态质量验收。

**迁移边界：** 0.2.0a1 的公共读取返回源姿态；0.2.0a2 默认返回 XYZW。需要完整旧读取
语义的审计或保真工作流可显式使用 `from edc import open_source_dataset`。
`transform_orientation(..., target=...)` 仍提供有意请求的 RPY/矩阵等变换，返回
`output / params / source_mapping`；这些显式变换不会改变默认中间层契约。

独立数组入口是 `from edc import convert_orientation`，要求明确给出 source 和 target。
转换返回新数组；保留 Episode、源文件、selector、实际与请求通道、行对应、时间来源、clock 及坐标证据。
只是表示转换，不改变参考坐标系、不重采样、不建立同步或确认标定，也不代替质量评估。
执行时 SI 视图从原始表示转换一次，避免度制 RPY 被重复换算。

RPY 输出使用规范角域；万向节锁行显式列在 `gimbal_lock_rows`，该行令 yaw=0、保留等价旋转。
四元数使用确定性符号，不提供跨时间符号展开；不能直接把表示跳变当作机器人运动突变。

## 兼容与实际支持

ROS Pose/Imu/Odometry 的消息 schema 明确规定源字段为 XYZW，现保存字段表示证据，
但不因此确认实际单位、clock 或校准。把该源重标成 RPY/WXYZ 会明确失败，应使用显式转换。
独立结构化姿态字段有完整表示声明时不再误入扁平 pose 的启发式切片。
源诊断入口保留旧读取行为；公共 orientation 读取若没有完整表示则明确阻塞，
不会根据数组长度或话题名称猜测编码。其他通道仍可独立读取。

通用 IMU 待绑定模板改为未知 orientation 维度/表示，不能预设所有源都为四元数。
已有 X1 原始配置、历史报告、LeRobot 图像/视频入口未修改；ROS 源可由消息 schema 提供表示。
本轮测试覆盖 JSON episode、HDF5、Parquet 和 ROS 源行为；官方 LeRobot 环境另外
验证了 RPY、矩阵以及手部/触觉数值与时间映射保真。它们是合成接口用例，
不据此声称所有 LeRobot 姿态编码或真实手部/触觉设备完成验收。

## 显式转换阶段的历史验收（2026-10-27）

- 新增 52 个回归用例；源码全量 **660 passed / 23 skipped**，源码外安装 wheel 全量
  **660 passed / 23 skipped**，安装 wheel 的官方 LeRobot/姿态/逻辑名/媒体专项
  **123 passed / 3 skipped**。这些集合重叠，不相加。
- 同一真实 X1 MCAP 的左右夹爪状态和指令四路均读出 position/velocity/effort；
  每字段分别为 12,070×1、12,069×1、4,283×1、4,286×1。源单位与 effort 的物理含义
  未由本次读取确认，不能据此判定真实夹爪物理质量。
- 独立 odometry 配置读出 2,414×4 的原始 XYZW，转换为 2,414×3 的弧度 RPY；
  旋转矩阵往返最大误差约 8.67e-19。源文件摘要、原数组、通道元数据和时间映射保持不变。
- 完整标准词表仍是 107 名称/21 模态；真实文件仍是 32 路源 topic/23 个逻辑绑定。
  本轮预览有 24 个视图（10 个真实数据视图、14 个待绑定声明），其中两种 odom 视图
  指向同一个源 topic，不能把视图数当作实际设备或源 topic 数。

以上为上一阶段结果，不代表新版自动读取已完成验证。新版验收见 [STATUS](STATUS.md)。
真实样例证据由维护者本地保存，不随 GitHub 源码分发。
