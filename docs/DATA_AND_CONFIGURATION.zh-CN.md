# 数据接入与配置

[返回主页](../README.zh-CN.md) · [English](DATA_AND_CONFIGURATION.md) · [快速开始](QUICKSTART.zh-CN.md)

<a id="formats"></a>
## 支持范围：数据格式与已有配置

读取器负责从文件中取出数值、图像和时间记录；机器人配置说明这些记录属于哪个部件、表示什么、使用什么单位。先选择匹配的格式和配置，再查看当前数据能运行哪些算法。

### 数据格式

| 格式 | 可以读取什么 | 使用要求 |
|---|---|---|
| LeRobot v2.1 / v3.0 | 数值列、支持的图像列及配套视频 | 保留描述各段操作的 metadata 和索引，以及视频中的对应范围；配置各列含义。官方读取方式需要 Python ≥3.12 与 `.[lerobot]`；也可使用 `.[formats]` 提供的读取方式 |
| HDF5 | 文件内的数值数组与图像 | 指定每段操作所在的组，例如 `data/demo_0`；一个文件可包含多段操作。配置数组名称、分量顺序和时间信息 |
| Parquet | 数值列及支持的图像列 | 非 LeRobot 数据须另行说明每段操作的起止行和时间列，不能把每个文件直接当成一段操作 |
| ROS1 / ROS2 bag | 已支持消息中的关节、位姿、IMU、图像及时间记录 | 需要 `rosbags`；文件中的消息定义须可读取。自定义消息还要确认所需字段能被提取 |
| MCAP | 已支持的 ROS1、ROS2 CDR 或 Protobuf 消息中的数值、图像及时间记录 | 需要 `mcap` 和对应解码库；文件内须有可用的消息定义，Protobuf 需内嵌描述文件。不能读取的内容会限制对应检查 |
| JSON＋视频目录 | 逐条数值记录和相机视频，如 X1 Pro 的 robot-bridge 导出目录 | JSON 结构、数据名称和视频文件布局须匹配读取器；数值与视频逐帧如何对应须明确 |
| 独立 MP4 | 视频画面及文件中的播放时间 | 可做图像／视频检查；单独的视频不提供关节位置，也不能用播放时间证明设备采集同步 |

格式依赖的安装见[快速开始](QUICKSTART.zh-CN.md)。读取器实现见 [`src/edc/data/readers/`](../src/edc/data/readers/)。图片和视频都可以检查画面；视频文件是否损坏等检查只适用于视频文件。

### 已有机器人配置

下表列出仓库已有的配置及其对应数据。选择时应同时核对文件格式和数据名称；同一机型采用不同采集方式时，也可能需要不同配置。

| 配置 | 对应数据 | 已配置内容与使用要求 |
|---|---|---|
| [X1 Pro · MCAP](../configs/robots/x1pro_mcap.yaml) | 快速开始使用的 MCAP 记录 | 双臂关节与末端、四路彩色相机及头部深度等；关节位置／速度和末端 XYZ 单位已登记。设备时钟关系、深度比例和部分数值限值仍未确认 |
| [X1 Pro · JSON＋视频](../configs/robots/x1pro.yaml) | robot-bridge 导出的 JSON＋三路 MP4 | 双臂、主从端数据和头部／腕部相机；数值名称为 `follow_*`、`master_*` 等。夹爪等部分单位未知，不能直接替代 MCAP 配置 |
| [乐聚 KUAVO 4pro](../configs/robots/leju_kuavo.yaml) | 灵巧手版 ROS1 bag | 双臂关节、灵巧手、IMU 和相机；末端位置由关节及 URDF 计算。深度比例未确认，不能做米制深度检查 |
| [珞石双臂](../configs/robots/luoshi.yaml) | 配置所对应的 LeRobot v3.0 数据 | 14 项关节数组按左7、右7拆分，三路彩色相机；无实际末端位置反馈，不能用末端指令替代；缺关节限位和速度上限 |
| [pepsi_sort 单臂](../configs/robots/pepsi_sort.yaml) | 配置所对应的 LeRobot v3.0 数据 | 末端位姿、夹爪和腕部相机；无关节角，夹爪单位未知。按帧号生成的时间不能用于采集同步检查 |
| [Franka Panda · 仿真](../configs/robots/franka_panda.yaml) | robomimic／robosuite 的 HDF5 数据 | 单臂关节、末端和夹爪；`data/demo_*` 分别保存每段操作。属于仿真数据，20 Hz 帧时间不是真实采集时间 |

这些配置描述对应数据的读取方式和检查参数，不代表同型号机器人的所有数据都能直接套用。算法能否运行仍取决于本次文件内容和配置是否齐全。

### 使用前具体核对什么

- **文件里的数据怎么存？** bag／MCAP 中的一条“消息”就是一条带时间的记录；“消息类型”说明里面有哪些字段、怎样存储。例如关节消息里可能有 `position` 和 `velocity`。如果厂家改用了自定义结构，读取器需要能解析它，才能取出数值；只识别文件后缀还不够。
- **取出的数值是什么？** 配置要说明 `position` 是关节角还是末端位置，用度、弧度还是米；`effort` 是电流还是力矩；记录的是下发指令还是实际反馈。这就是需要确认的数据含义，不根据字段名猜测。
- **图像和时间能否用于所选检查？** 图像需要能解码成画面。深度还需说明一个读数对应多少米、哪些值表示测量无效；时间则需说明记录时刻及单位。比较不同设备时，还要确认它们的时钟关系，详见[单位与时间](#units-time)。

<a id="dataset"></a>
## 1. 查看字段与轨迹

```bash
python examples/quickstart/list_inputs.py /path/to/dataset/episode.mcap
```

示例脚本使用公共 IR。当前 `edc channels` 命令在本例 MCAP 上会因缺少 `Episode.info` 报错，因此此处采用脚本查看，读取器与算法保持不变。

输出的源 topic／feature 填入配置的 `source`。保留实际字段名与分量顺序：关节位置、速度、effort 不能只凭消息名确定物理含义。

一份数据集可以包含多次操作，每次操作里又有各路关节、相机等记录。例如，100 次抓取可以构成一个数据集，每次抓取分别检查左臂、右臂和相机数据；代码中分别称为 `Dataset`（数据集）、`Episode`（一段操作）和 `Channel`（一路数据）。

每段操作从哪里开始和结束，以数据中已有的说明或显式配置为准，不靠文件数量猜测。LeRobot 使用其 metadata；多段操作共存在一个 HDF5 中时，可用 `data.episode_group` 指定存放各段操作的组。自定义集合可通过 `dataset.yaml` 列出每段操作，例如：

```yaml
schema_version: 1
id: my_task_dataset
version: '1'
episodes:
  - id: take_001
    path: episode.mcap
    format: mcap
    selector: {}
```

相对数据路径以清单目录为准；本例只有在一次完整采集确实对应一个文件时才成立。一个文件中的多个轨迹必须使用对应读取器支持的 selector，不按文件数量推断轨迹数量。原始数据只读，输出放到独立目录。

<a id="robot"></a>
## 2. 机器人与算法配置

从 [single_arm.yaml](../configs/templates/single_arm.yaml)、[dual_arm.yaml](../configs/templates/dual_arm.yaml) 或 [dual_arm_hand.yaml](../configs/templates/dual_arm_hand.yaml) 开始。机器人 YAML 的主要字段：

| 字段 | 职责 |
|---|---|
| `robot` | 名称、型号、确认人；`name` 以英文字母开头，`confirmed_by` 必填 |
| `data` | 显式时间列、无采集时间的说明、最短时长、HDF5 轨迹组等 |
| `arms` | 实际臂、关节／末端反馈、指令、夹爪／手、Home 与可选 URDF |
| `cameras` | 源字段、相机角色、RGB／深度和腕部归属 |
| `imu` / `other` | IMU 分量或其他明确登记的数值 |
| `checks` | 算法参数、通道选择及本次检查范围 |

典型单臂配置（字段名必须按自己的数据修改）：

```yaml
robot:
  name: my_robot
  confirmed_by: data_owner
arms:
  main:
    joints: {source: observation.state, index: 0-5, unit: rad}
    eef_position: {source: observation.eef, index: 0-2, unit: m}
cameras:
  head: {source: observation.images.head, mount: head}
checks:
  Numeric_Check: {arms: all}
```

`source` 是实际字段，`field` 选择消息内成员（如 `position`），`index` 选择分量。`0-5` 含两端；也可写 `[0, 2, 4]`。关节声明需要明确分量数量。派生的标准逻辑名如 `joint/arm/main/feedback` 用于算法绑定；逻辑名不会把指令变成反馈。

**机器人声明与本次范围分开：**

| 机器人声明 | 算法参数 | 检查范围 |
|---|---|---|
| `main` | `arms: all` | 唯一机械臂 |
| `left`、`right` | `arms: all` | 两侧分别检查 |
| `left`、`right` | `arms: [left]` | 只检查左侧及明确保留的共享输入 |
| `left`、`right` | `arms: [right]` | 只检查右侧及明确保留的共享输入 |

左右合存时声明不同 `index`；分存时声明各自 `source`。已被分量视图覆盖的原字段不再整数组重复检查。缺失另一侧或归属不明会说明缺失范围，不能自动当成单臂完整通过。[范围说明](EVIDENCE_AND_SCOPE.md)

同一机器人可引用于多个任务，任务与 episode 信息属于数据集。换采集批次后，仍需核对源字段、单位、标定及 clock 的适用范围。

```bash
edc robot check --robot /path/to/robot.yaml --data /path/to/dataset/episode.mcap
```

该命令用于上述机器人卡片 YAML 的字段核对；读到值不等于已经证明物理含义。快速开始的 [`x1pro_mcap.yaml`](../configs/robots/x1pro_mcap.yaml) 是完整 profile，显式保留逐字段确认及未知 clock；直接供 `open_dataset(profile=...)`、`run --robot` 与 `batch --robot` 使用，不经 `compile_card()` 再编译。

<a id="units-time"></a>
## 3. 单位与时间

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
- 选择头部相机作参考，不等于确认共同 clock。算法各自的时间条件见[算法指南](ALGORITHMS.zh-CN.md)。

<a id="urdf"></a>
## 4. URDF：获取关节信息并核对

URDF 可以提供链、关节顺序、限位、速度上限及正运动学；它不能自动确定数据字段、指令／反馈、时钟、相机、任务或全部单位。

```bash
edc robot urdf --urdf /path/to/robot.urdf
edc robot urdf --urdf /path/to/robot.urdf --base base_link --tip tool_link
```

把返回的链信息按**数据的关节列顺序**写入对应臂：

```yaml
urdf:
  file: robot/arm.urdf
  joints: [joint1, joint2, joint3, joint4, joint5, joint6]
  base_link: base_link
  tip_link: tool_link
  # tip_offset: [0, 0, 0.1]  # 数据所指工作点在 tip_link 中的位置，m
```

URDF 文件路径相对机器人 YAML。关节方向、零位、链与数据必须一致；仅支持已展开的普通 URDF。`eef_position: fk` 可从已配置的关节与链计算末端位置，不代表末端已实测验证。

```bash
edc robot urdf --robot /path/to/robot.yaml \
  --data /path/to/episode_1 /path/to/episode_2
```

有记录末端时，用第一条轨迹求坐标关系，再在第二条核对。未记录末端或没有两条可用轨迹时，只能做可用的关节核对。动作不丰富、坐标关系可吸收的误差和小偏差可能无法辨别。Home、容差、末端工作点及标定适用范围仍须明确配置。
