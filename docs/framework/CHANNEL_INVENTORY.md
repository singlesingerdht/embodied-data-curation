# 中间层通道逐项清单

生成依据：安装词表版本 2026.10.27；原 38 项保留，当前 **107 项／21 模态**。这是命名目录，不是 107 项解码或算法验收。使用边界与参数文件见 [CHANNEL_REGISTRY](CHANNEL_REGISTRY.md)。

## 标准目录

同位置多个实例使用 @primary、@backup 等明确后缀。下面为基础名称；side=main 是保留的历史命名约束，不据此补填左右属性。

### 彩色图像 / rgb（6）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| rgb/head | camera_role=head |
| rgb/chest | camera_role=chest |
| rgb/wrist/left | camera_role=wrist；side=left |
| rgb/wrist/right | camera_role=wrist；side=right |
| rgb/wrist/main | camera_role=wrist |
| rgb/main | 仅校验 modality；不补齐未知物理属性 |

### 深度图 / depth（6）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| depth/head | camera_role=head |
| depth/chest | camera_role=chest |
| depth/main | 仅校验 modality；不补齐未知物理属性 |
| depth/wrist/left | side=left |
| depth/wrist/right | side=right |
| depth/wrist/main | 仅校验 modality；不补齐未知物理属性 |

### 红外图像 / infrared（6）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| infrared/main | 仅校验 modality；不补齐未知物理属性 |
| infrared/head | 仅校验 modality；不补齐未知物理属性 |
| infrared/chest | 仅校验 modality；不补齐未知物理属性 |
| infrared/wrist/left | side=left |
| infrared/wrist/right | side=right |
| infrared/wrist/main | 仅校验 modality；不补齐未知物理属性 |

### 热成像 / thermal（1）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| thermal/main | 仅校验 modality；不补齐未知物理属性 |

### 惯性测量 / imu（7）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| imu/main | 仅校验 modality；不补齐未知物理属性 |
| imu/base | 仅校验 modality；不补齐未知物理属性 |
| imu/head | 仅校验 modality；不补齐未知物理属性 |
| imu/chest | 仅校验 modality；不补齐未知物理属性 |
| imu/wrist/left | side=left |
| imu/wrist/right | side=right |
| imu/wrist/main | side=main |

### 关节空间 / joint_space（42）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| joint/arm/left/feedback | side=left；source_kind=feedback |
| joint/arm/left/command | side=left；source_kind=command |
| joint/arm/right/feedback | side=right；source_kind=feedback |
| joint/arm/right/command | side=right；source_kind=command |
| joint/arm/main/feedback | side=main；source_kind=feedback |
| joint/arm/main/command | side=main；source_kind=command |
| joint/gripper/left/state | side=left；device_type=gripper |
| joint/gripper/left/feedback | side=left；device_type=gripper；source_kind=feedback |
| joint/gripper/left/command | side=left；device_type=gripper；source_kind=command |
| joint/gripper/right/state | side=right；device_type=gripper |
| joint/gripper/right/feedback | side=right；device_type=gripper；source_kind=feedback |
| joint/gripper/right/command | side=right；device_type=gripper；source_kind=command |
| joint/gripper/main/state | side=main；device_type=gripper |
| joint/gripper/main/feedback | side=main；device_type=gripper；source_kind=feedback |
| joint/gripper/main/command | side=main；device_type=gripper；source_kind=command |
| joint/head/state | body_part=head |
| joint/lift/state | body_part=lift |
| joint/hand/left/state | side=left；device_type=dexterous_hand |
| joint/hand/left/feedback | side=left；source_kind=feedback；device_type=dexterous_hand |
| joint/hand/left/command | side=left；source_kind=command；device_type=dexterous_hand |
| joint/hand/right/state | side=right；device_type=dexterous_hand |
| joint/hand/right/feedback | side=right；source_kind=feedback；device_type=dexterous_hand |
| joint/hand/right/command | side=right；source_kind=command；device_type=dexterous_hand |
| joint/hand/main/state | device_type=dexterous_hand |
| joint/hand/main/feedback | source_kind=feedback；device_type=dexterous_hand |
| joint/hand/main/command | source_kind=command；device_type=dexterous_hand |
| joint/leg/left/state | side=left |
| joint/leg/left/feedback | side=left；source_kind=feedback |
| joint/leg/left/command | side=left；source_kind=command |
| joint/leg/right/state | side=right |
| joint/leg/right/feedback | side=right；source_kind=feedback |
| joint/leg/right/command | side=right；source_kind=command |
| joint/base/state | body_part=base |
| joint/base/feedback | body_part=base；source_kind=feedback |
| joint/base/command | body_part=base；source_kind=command |
| joint/torso/state | body_part=torso |
| joint/torso/feedback | body_part=torso；source_kind=feedback |
| joint/torso/command | body_part=torso；source_kind=command |
| joint/head/feedback | body_part=head；source_kind=feedback |
| joint/head/command | body_part=head；source_kind=command |
| joint/lift/feedback | body_part=lift；source_kind=feedback |
| joint/lift/command | body_part=lift；source_kind=command |

### 空间位姿 / pose（9）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| pose/arm/left/feedback | side=left；source_kind=feedback |
| pose/arm/left/command | side=left；source_kind=command |
| pose/arm/right/feedback | side=right；source_kind=feedback |
| pose/arm/right/command | side=right；source_kind=command |
| pose/arm/main/feedback | side=main；source_kind=feedback |
| pose/arm/main/command | side=main；source_kind=command |
| pose/base/feedback | source_kind=feedback |
| pose/base/command | source_kind=command |
| pose/tracker/main | 仅校验 modality；不补齐未知物理属性 |

### 空间线速度与角速度 / twist（2）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| twist/base/feedback | source_kind=feedback |
| twist/base/command | source_kind=command |

### 力与力矩 / wrench（6）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| wrench/arm/left/feedback | source_kind=feedback；side=left |
| wrench/arm/left/command | source_kind=command；side=left |
| wrench/arm/right/feedback | source_kind=feedback；side=right |
| wrench/arm/right/command | source_kind=command；side=right |
| wrench/arm/main/feedback | source_kind=feedback |
| wrench/arm/main/command | source_kind=command |

### 触觉 / tactile（3）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| tactile/hand/left | side=left |
| tactile/hand/right | side=right |
| tactile/hand/main | 仅校验 modality；不补齐未知物理属性 |

### 接触事件/状态 / contact（4）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| contact/foot/left | side=left |
| contact/foot/right | side=right |
| contact/hand/left | side=left |
| contact/hand/right | side=right |

### 点云 / point_cloud（2）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| point_cloud/main | 仅校验 modality；不补齐未知物理属性 |
| point_cloud/base | 仅校验 modality；不补齐未知物理属性 |

### 距离扫描 / range_scan（1）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| range_scan/base | 仅校验 modality；不补齐未知物理属性 |

### 事件相机事件流 / event_camera（1）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| event_camera/main | 仅校验 modality；不补齐未知物理属性 |

### 音频 / audio（1）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| audio/main | 仅校验 modality；不补齐未知物理属性 |

### 里程计 / odometry（1）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| odometry/base | 仅校验 modality；不补齐未知物理属性 |

### 卫星定位 / gnss（1）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| gnss/main | 仅校验 modality；不补齐未知物理属性 |

### 设备状态/电池/温度等 / system_status（3）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| system_status/robot | 仅校验 modality；不补齐未知物理属性 |
| system_status/battery | 仅校验 modality；不补齐未知物理属性 |
| system_status/motor | 仅校验 modality；不补齐未知物理属性 |

### 任务文本 / task_text（1）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| task_text/instruction | 仅校验 modality；不补齐未知物理属性 |

### 标注/标签 / annotation（3）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| annotation/main | 仅校验 modality；不补齐未知物理属性 |
| annotation/calibration | 仅校验 modality；不补齐未知物理属性 |
| annotation/intervention | 仅校验 modality；不补齐未知物理属性 |

### 未分类 / other（1）

| 逻辑名 | 显式属性的冲突校验 |
|---|---|
| other/main | 仅校验 modality；不补齐未知物理属性 |

## 指定 X1 MCAP 的 32 路源 topic

以下绑定来自现有 configs/robots/x1_pro.yaml；消息类型和数量复用已核查的 源 topic 证据（本地留存：evidence/imu_configuration_20261027/source-topics.json）。本轮没有重新扫描全部数据或变更这份真实 profile。

**23 路已绑定，9 路未绑定。** 已绑定的 odometry、深度等仍须按字段／编码单独验收。

| 源 topic | 消息类型 | 消息数 | 当前逻辑名 | 当前模态 |
|---|---|---:|---|---|
| /master_left_arm/end_pose | geometry_msgs/msg/PoseStamped | 4316 | pose/arm/left/command | pose |
| /master_left_arm/joint_states | sensor_msgs/msg/JointState | 4316 | joint/arm/left/command | joint_space |
| /master_left_gripper/joint_states | sensor_msgs/msg/JointState | 4283 | joint/gripper/left/command | joint_space |
| /master_right_gripper/joint_states | sensor_msgs/msg/JointState | 4286 | joint/gripper/right/command | joint_space |
| /master_right_arm/end_pose | geometry_msgs/msg/PoseStamped | 4398 | pose/arm/right/command | pose |
| /master_right_arm/joint_states | sensor_msgs/msg/JointState | 4398 | joint/arm/right/command | joint_space |
| /tracked_pose | geometry_msgs/msg/PoseStamped | 2898 | x1pro__tracking/pose | pose |
| /camera1/usb_cam1/image_raw/image_compressed | sensor_msgs/msg/CompressedImage | 1449 | rgb/wrist/left | rgb |
| /camera3/usb_cam3/image_raw/image_compressed | sensor_msgs/msg/CompressedImage | 1449 | rgb/wrist/right | rgb |
| /left_arm/end_pose | geometry_msgs/msg/PoseStamped | 12069 | pose/arm/left/feedback | pose |
| /left_arm/joint_states | sensor_msgs/msg/JointState | 12069 | joint/arm/left/feedback | joint_space |
| /left_arm/wrench_ext_local | geometry_msgs/msg/WrenchStamped | 12069 | 未绑定 | other |
| /left_arm/wrench_ext_world | geometry_msgs/msg/WrenchStamped | 12070 | 未绑定 | other |
| /right_arm/end_pose | geometry_msgs/msg/PoseStamped | 12070 | pose/arm/right/feedback | pose |
| /whole_body/left_arm/end_pose | geometry_msgs/msg/PoseStamped | 12070 | pose/arm/left/feedback@whole_body | pose |
| /right_arm/joint_states | sensor_msgs/msg/JointState | 12070 | joint/arm/right/feedback | joint_space |
| /right_arm/wrench_ext_local | geometry_msgs/msg/WrenchStamped | 12069 | 未绑定 | other |
| /right_arm/wrench_ext_world | geometry_msgs/msg/WrenchStamped | 12070 | 未绑定 | other |
| /lift/joint_states | sensor_msgs/msg/JointState | 12069 | joint/lift/state | joint_space |
| /whole_body/right_arm/end_pose | geometry_msgs/msg/PoseStamped | 12069 | pose/arm/right/feedback@whole_body | pose |
| /left_gripper/joint_states | sensor_msgs/msg/JointState | 12070 | joint/gripper/left/state | joint_space |
| /right_gripper/joint_states | sensor_msgs/msg/JointState | 12069 | joint/gripper/right/state | joint_space |
| /odom | nav_msgs/msg/Odometry | 2414 | odometry/base | odometry |
| /head/joint_states | sensor_msgs/msg/JointState | 2414 | joint/head/state | joint_space |
| /camera_head_front/color/image_raw/compressed | sensor_msgs/msg/CompressedImage | 1435 | rgb/head | rgb |
| /camera_head_front/depth/image_raw/compressedDepth | sensor_msgs/msg/CompressedImage | 483 | depth/head | depth |
| /camera_chest_front/color/image_raw/compressed | sensor_msgs/msg/CompressedImage | 1454 | rgb/chest | rgb |
| /diversity_data | std_msgs/msg/String | 1 | 未绑定 | other |
| /distribute_data | std_msgs/msg/String | 1 | 未绑定 | other |
| /instruction_data | std_msgs/msg/String | 1 | 未绑定 | other |
| /take_over_data | std_msgs/msg/String | 1 | 未绑定 | other |
| /calibration_data | std_msgs/msg/String | 1 | 未绑定 | other |

未绑定的四路 WrenchStamped 和五路 String 是现样例优先补齐项；没有 IMU 消息。不能把 /odom 当成 IMU。机器可读对照见 source-coverage.json（本地留存：evidence/channel_registry_20261027/source-coverage.json）。
