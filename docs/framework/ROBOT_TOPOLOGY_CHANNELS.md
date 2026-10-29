# 按机器人构型维护逻辑通道

odom 和 IMU 使用同一 Dataset → Episode → Channel 中间层，分别保持 odometry 和 imu 类型。名称用于稳定寻址，parts 描述实际构型，dataset.source_bindings 绑定当前录制的真实源名。算法通过声明的逻辑通道获取字段；名字相似不代表物理含义、单位或坐标系相同。

## 四层配置如何配合

| 配置位置 | 负责什么 | 可以自定义什么 |
|---|---|---|
| channel_registry.yaml / 团队词表 | 逻辑通道的名称及语义约束 | 标准名、@实例；新团队名采用 team__ 前缀 |
| robot.parts / robot.channels | 部件、归属、字段、维度、单位、安装和标定 | 机器人 id、部件稳定 ID、构型及通道声明 |
| dataset.source_bindings / capture | 当前批次真实 topic 或 feature，适用范围内的物理与时间证据 | 源名称；未绑定用 null，明确阻塞执行 |
| algorithms.methods.*.params | 算法选择哪些通道及其参数 | channels 指定标准名或团队名；所需字段必须实际可读 |

文件名也可自定义，修改总配置中的 ref 即可；ref 相对包含它的配置文件解析。robot.id 必须以英文字母开头，后续使用字母、数字、下划线或连字符。部件 ID 是非空稳定字符串；名称不决定左右侧或机械臂关系。开发者扩展 ID 与通道词表是独立注册体系。

## 双臂移动机器人例子

以下是完整例子的简化片段，不声称真实设备存在这些 topic：

    # robot.yaml
    schema_version: 1
    id: my_mobile_robot
    version: '1'
    parts:
      base: {type: base}
      left: {type: arm, side: left}
      right: {type: arm, side: right}
      left_wrist: {type: sensor_mount, side: left, arm: left}
      right_wrist: {type: sensor_mount, side: right, arm: right}
    channels:
      odometry/base:
        logical_name: odometry/base
        modality: odometry
        robot_part: base
        fields: [position, orientation, lin_vel, ang_vel, pose_covariance, twist_covariance]
      imu/wrist/right:
        logical_name: imu/wrist/right
        modality: imu
        robot_part: right_wrist
        side: right
        fields: [ang_vel, lin_acc, orientation]

    # dataset.yaml（这里只是名称示意，必须先核对源文件）
    path: /path/to/recording.mcap
    source_bindings:
      odometry/base: /odom
      imu/wrist/right: /sensors/right_wrist/imu

    # algorithms.yaml
    methods:
      Numeric_Check:
        params:
          channels: [odometry/base, imu/wrist/right]

右腕已有标准名 imu/wrist/right，左腕是 imu/wrist/left；先前只展示左腕并不意味着只支持左腕。两路须分别绑定，缺右路会明确报缺输入，不回退左路。单臂可用 imu/wrist/main，但 main 是通道/部件身份，不是新的物理左右侧。

只显式选择底盘里程计时不要求同时评估左右臂；显式选择某腕部而源通道缺失，仍保留缺失声明和未完成状态。arms: all 只展开 type: arm，不把四条腿、底盘或传感器安装座变成机械臂。

## 不同构型使用不同 profile

| 构型 | parts 示例 | 通道示例 | 可编辑配置 |
|---|---|---|---|
| 单臂 | main、main_wrist | imu/wrist/main | [single_arm_sensors.yaml](../../configs/robots/single_arm_sensors.yaml) |
| 双臂移动机器人 | base、left/right、left_wrist/right_wrist | odometry/base、imu/base、左右腕 IMU | [dual_arm_mobile_sensors.yaml](../../configs/robots/dual_arm_mobile_sensors.yaml) |
| 四足 | base、front_left/front_right、rear_left/rear_right | imu/base、lab__imu/leg/front_left | [quadruped_sensors.yaml](../../configs/robots/quadruped_sensors.yaml) |

四足例子在 [团队词表](../../configs/channel_registries/quadruped_example.yaml) 登记四个腿部 IMU 名，但 profile 只展示前左腿的一路声明；词表可选名称不代表设备已装齐传感器。其余位置按真实硬件添加，不推测。机器人配置分别维护，不要求所有机器人共用一套部件列表。每个机器人按其有的部件声明，多个机器人数据集按对应 profile 读取。

例如自定义 acme__imu/leg/front_right：在团队词表登记 modality: imu、side: right，在机器人通道中设 robot_part: front_right，再在数据侧绑定实际源名；算法 channels 填同一逻辑名。@backup 可区分同位置第二个实例，必须精确选择，不自动替代主传感器。

当前 Numeric_Check、Dedup_Check 等已有通道参数的算法可这样选择；专用位姿/关节算法仍要求各自的模态、角色、单位、时间和标定。自定义部件 ID 时给位姿算法显式 channel/channels，不依赖默认 pose/arm/{arm_id}/feedback 的构造。新算法通过 AlgorithmSpec 与 run(context, params) 和 edc.algorithms 插件接入。

## odometry 本次补齐与仍有的边界

ROS1、ROS2 SQLite、独立 MCAP 的 nav_msgs/Odometry 读取六类原始字段：position[3]、orientation[4，XYZW]、lin_vel[3]、ang_vel[3]、pose_covariance[36]、twist_covariance[36]。保留 NaN/Inf；不生成 lin_acc，不把速度求差后当作传感器加速度。独立 geometry_msgs/Twist 解码不包含在本次实现中。

frame_evidence 按原始行段保存 header.frame_id 与 child_frame_id 及其变化，分别关联 pose 和 twist 字段，区间为 [start_row, stop_row)。它是源声明证据，不自动确认标定、物理坐标变换或全通道共同坐标系。混合量协方差也不填成单一物理单位。

时间默认使用记录的 container 轴；显式选 acquisition 才使用逐行对应的 header.stamp，缺失不回退成采集时间。两套时间不重采样，不自动声明共同 clock。真正跨通道比较仍须共同 clock 或显式映射。

[configs/odometry.yaml](../../configs/odometry.yaml) 提供 robot/dataset/algorithm/workflow 入口。source_bindings 默认 null，须绑定后才执行。Numeric_Check 对六字段做有限性检查；Dedup_Check 检查所选通道的记录时间重复。odom 漂移、位姿/速度运动学一致性、协方差合理性等专用物理判据尚未实现，也不会套用 IMU 偏置或重力检测。执行 completed 不等于完整质量通过；缺物理覆盖保持 unassessed。

旧 X1 profile 的 odometry/base 仍明确 fields: []，保持原声明。使用新的 opt-in 配置或审阅后显式补字段才能启用；本次未改其真实单位/校准。真实样例没有 IMU，不以 odom 冒充。测试和本次单文件实跑边界见 验收记录（本地留存：evidence/odometry_topology_20261027/README.md）。

兼容边界：新增 frame_evidence、catalog 的 robot_part/part_definition；Odometry 消息现在按 schema 识别为 odometry，若旧配置把它强制声明为 imu/pose 等其他模态会明确拒绝，应改为 odometry，或由显式变换生成新派生通道。安装座引用未声明/非 arm 部件，以及明确左右侧冲突会拒绝。历史报告不改写。
