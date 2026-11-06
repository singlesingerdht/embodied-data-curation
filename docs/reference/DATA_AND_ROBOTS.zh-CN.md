# 数据格式与机器人配置参考

[返回主页](../../README.zh-CN.md) · [English](DATA_AND_ROBOTS.md) · [接入数据并配置机器人](../how-to/DATA_AND_ROBOT.zh-CN.md)

本页用于**查阅**：支持哪些数据格式、仓库已有哪些机器人配置、机器人 YAML 有哪些字段，以及单位与时间的声明方式。
具体操作步骤见[接入数据并配置机器人](../how-to/DATA_AND_ROBOT.zh-CN.md)；格式依赖的安装见[快速开始](../tutorial/QUICKSTART.zh-CN.md)。

读取器负责从文件中取出数值、图像和时间记录；机器人配置说明这些记录属于哪个部件、表示什么、使用什么单位。先选择匹配的格式和配置，再查看当前数据能运行哪些算法。

<a id="formats"></a>
## 数据格式

| 格式 | 可以读取什么 | 使用要求 |
|---|---|---|
| LeRobot v2.1 / v3.0 | 数值列、支持的图像列及配套视频 | 保留描述各段操作的 metadata 和索引，以及视频中的对应范围；配置各列含义。官方读取方式需要 Python ≥3.12 与 `.[lerobot]`；也可使用 `.[formats]` 提供的读取方式 |
| HDF5 | 文件内的数值数组与图像 | 指定每段操作所在的组，例如 `data/demo_0`；一个文件可包含多段操作。配置数组名称、分量顺序和时间信息 |
| Parquet | 数值列及支持的图像列 | 使用 `episode_index`（或 profile 指定的 `episode_column`）划分轨迹；无该列时整个文件作为一条轨迹 |
| ROS1 / ROS2 bag | 已支持消息中的关节、位姿、IMU、图像及时间记录 | 需要 `rosbags`；文件中的消息定义须可读取。自定义消息还要确认所需字段能被提取 |
| MCAP | 已支持的 ROS1、ROS2 CDR 或 Protobuf 消息中的数值、图像及时间记录 | 需要 `mcap` 和对应解码库；文件内须有可用的消息定义，Protobuf 需内嵌描述文件。不能读取的内容会限制对应检查 |
| JSON＋视频目录 | 逐条数值记录和相机视频，如 X1 Pro 的 robot-bridge 导出目录 | JSON 结构、数据名称和视频文件布局须匹配读取器；数值与视频逐帧如何对应须明确 |
| 独立 MP4 | 视频画面及文件中的播放时间 | 可做图像／视频检查；单独的视频不提供关节位置，也不能用播放时间证明设备采集同步 |

读取器实现见 [`src/edc/data/readers/`](../../src/edc/data/readers/)。图片和视频都可以检查画面；视频文件是否损坏等检查只适用于视频文件。

<a id="configs"></a>
## 已有机器人配置

下表列出仓库已有的配置及其对应数据。选择时应同时核对文件格式和数据名称；同一机型采用不同采集方式时，也可能需要不同配置。

| 配置 | 对应数据 | 已配置内容与使用要求 |
|---|---|---|
| [X1 Pro · MCAP](../../configs/robots/x1pro_mcap.yaml) | 快速开始使用的 MCAP 记录 | 双臂关节与末端、四路彩色相机及头部深度等；关节位置／速度和末端 XYZ 单位已登记。设备时钟关系、深度比例和部分数值限值仍未确认 |
| [X1 Pro · JSON＋视频](../../configs/robots/x1pro.yaml) | robot-bridge 导出的 JSON＋三路 MP4 | 双臂、主从端数据和头部／腕部相机；数值名称为 `follow_*`、`master_*` 等。夹爪等部分单位未知，不能直接替代 MCAP 配置 |
| [乐聚 KUAVO 4pro](../../configs/robots/leju_kuavo.yaml) | 灵巧手版 ROS1 bag | 双臂关节、灵巧手、IMU 和相机；末端位置由关节及 URDF 计算。深度比例未确认，不能做米制深度检查 |
| [珞石双臂](../../configs/robots/luoshi.yaml) | 配置所对应的 LeRobot v3.0 数据 | 14 项关节数组按左7、右7拆分，三路彩色相机；无实际末端位置反馈，不能用末端指令替代；缺关节限位和速度上限 |
| [pepsi_sort 单臂](../../configs/robots/pepsi_sort.yaml) | 配置所对应的 LeRobot v3.0 数据 | 末端位姿、夹爪和腕部相机；无关节角，夹爪单位未知。按帧号生成的时间不能用于采集同步检查 |
| [Franka Panda · 仿真](../../configs/robots/franka_panda.yaml) | robomimic／robosuite 的 HDF5 数据 | 单臂关节、末端和夹爪；`data/demo_*` 分别保存每段操作。属于仿真数据，20 Hz 帧时间不是真实采集时间 |

这些配置描述对应数据的读取方式和检查参数，不代表同型号机器人的所有数据都能直接套用。算法能否运行仍取决于本次文件内容和配置是否齐全。

## 使用前具体核对什么

- **文件里的数据怎么存？** bag／MCAP 中的一条“消息”就是一条带时间的记录；“消息类型”说明里面有哪些字段、怎样存储。例如关节消息里可能有 `position` 和 `velocity`。如果厂家改用了自定义结构，读取器需要能解析它，才能取出数值；只识别文件后缀还不够。
- **取出的数值是什么？** 配置要说明 `position` 是关节角还是末端位置，用度、弧度还是米；`effort` 是电流还是力矩；记录的是下发指令还是实际反馈。这就是需要确认的数据含义，不根据字段名猜测。
- **图像和时间能否用于所选检查？** 图像需要能解码成画面。深度还需说明一个读数对应多少米、哪些值表示测量无效；时间则需说明记录时刻及单位。比较不同设备时，还要确认它们的时钟关系，详见[单位与时间](#units-time)。

<a id="robot-fields"></a>
## 机器人 YAML 字段

从 [single_arm.yaml](../../configs/templates/single_arm.yaml)、[dual_arm.yaml](../../configs/templates/dual_arm.yaml) 或 [dual_arm_hand.yaml](../../configs/templates/dual_arm_hand.yaml) 开始。机器人 YAML 的主要字段：

| 字段 | 职责 |
|---|---|
| `robot` | 名称、确认人、签名；`name` 以英文字母开头，`confirmed_by` 必填；`signature` 由 `edc batch` 在 yaml 改过后提示签名时写入，不要手改 |
| `data` | 显式时间列、无采集时间的说明、最短时长、HDF5 轨迹组等 |
| `arms` | 实际臂、关节／末端反馈、指令、夹爪／手、Home 与可选 URDF |
| `cameras` | 源字段、相机角色、RGB／深度和腕部归属；`info` 写对应的相机内参话题，结构检查据此配对 |
| `imu` / `other` | IMU 分量或其他明确登记的数值 |
| `checks` | 算法参数、通道选择及本次检查范围；`skip: [算法ID]` 跳过某项检查 |

`source` 是实际字段，`field` 选择消息内成员（如 `position`），`index` 选择分量。`0-5` 含两端；也可写 `[0, 2, 4]`。关节声明需要明确分量数量。派生的标准逻辑名如 `joint/arm/main/feedback` 用于算法绑定；逻辑名不会把指令变成反馈。

<a id="scope"></a>
### 机器人声明与本次检查范围

**机器人声明与本次范围分开：**

| 机器人声明 | 算法参数 | 检查范围 |
|---|---|---|
| `main` | `arms: all` | 唯一机械臂 |
| `left`、`right` | `arms: all` | 两侧分别检查 |
| `left`、`right` | `arms: [left]` | 只检查左侧及明确保留的共享输入 |
| `left`、`right` | `arms: [right]` | 只检查右侧及明确保留的共享输入 |

左右合存时声明不同 `index`；分存时声明各自 `source`。已被分量视图覆盖的原字段不再整数组重复检查。缺失另一侧或归属不明会说明缺失范围，不能自动当成单臂完整通过。[范围说明](EVIDENCE_AND_SCOPE.md)

同一机器人可引用于多个任务，任务与 episode 信息属于数据集。换采集批次后，仍需核对源字段、单位、标定及 clock 的适用范围。

<a id="units-time"></a>
## 单位与时间

### 单位

在**官方执行器提供的算法上下文**中，已确认的物理字段按 SI 读取。典型转换为 `90 deg → π/2 rad`、`1000 mm → 1 m`。原始数组保持不变。

| 卡片字段 | 可声明源单位／表示 | 算法使用 |
|---|---|---|
| `joints` | `rad`、`deg` | `rad`；当前主机械臂卡片只支持转动关节 |
| `joint_velocity` | `rad/s`、`deg/s` | `rad/s`；须配置速度上限或 URDF 限值 |
| `eef_position` | `m`、`cm`、`mm` | `m` |
| `eef_orientation` | `quaternion`＋`order: xyzw/wxyz`，或 `rpy_rad/rpy_deg` | 明确归一化后的 XYZW 四元数 |
| 夹爪／手／`other` | 支持的物理单位、`raw` 或 `unknown` | 按物理量、原值范围或有限性分别检查 |
| IMU | 已确认加速度与角速度单位 | `m/s^2`、`rad/s`；重力含义另行说明 |

`raw` 表示无物理单位的读数，卡片中须同时给 `range`；`unknown` 表示不知道，不能拿它代替无量纲 `1`。`joints.unit: unknown` 或 `m` 不被当前卡片接受；未知数值可放入支持 `unknown` 的 `other`，但不能因此获得关节物理算法资格。

已知原始编码与物理值关系时，可显式写 `scale: {raw: [0, 4095], value: [-3.1416, 3.1416]}` 并声明 `unit: rad`。这需要数据提供者确认，不能靠归一化推断单位。

普通 `open_dataset()` 返回的源读取视图不能一概当作完整 SI 数值视图；插件应使用执行器传入的 `context.episode`／`check()` 的 episode。源值诊断使用 `inspect_recorded_signal()`。当前并非所有物理模态都完成转换支持，不能将规范目标写成已实现能力。

### 时间

区分 acquisition（采集）、container（容器记录）、playback（播放）、synthetic（生成）、unknown。单位换算、clock 映射与重采样分别记录。

- MCAP／bag 可能同时有消息 header 和容器记录时间；缺 header 或共同 clock 时，不能仅凭文件格式证明同步。
- LeRobot 通常有共享行时间；只有数据确实保存原始采集时间时，才可绑定为采集依据。共享行时间不能冒充每路独立时间。
- 卡片可用 `data.time: {source: source_timestamp, unit: ns}` 绑定明确的原时间列；没有时用 `data.time_note` 说明。不能填入一个生成的索引列并声称是真实采集时间。
- 卡片编译产生的 clock 标识不是设备同步的实测证据；跨通道使用前须核实这一声明。未知或不同 clock 使用完整 profile 保留未知状态或显式映射。
- 选择头部相机作参考，不等于确认共同 clock。算法各自的时间条件见[内置质量算法参考](ALGORITHMS.zh-CN.md)。

## 相关页面

- [接入数据并配置机器人](../how-to/DATA_AND_ROBOT.zh-CN.md)：查看字段、编写配置、URDF 核对。
- [推荐、选择并运行算法](../how-to/RUN_AND_SELECT.zh-CN.md)：确认当前配置能运行哪些检查。
- [检查范围与证据参数参考](EVIDENCE_AND_SCOPE.md)：机械臂范围、时间差证据与画面联合复核的详细参数。
