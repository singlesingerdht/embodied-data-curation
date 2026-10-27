# 中间层 topic 目录与参数文件

本页说明 EDC 当前中间层的覆盖范围。topic 是源数据里的实际名称；逻辑通道名是框架配置的稳定寻址。二者显式绑定，源文件、源字段和各自时间轴不改写。

## 现在覆盖了什么

本次之前有 **38 个标准逻辑名**，集中于 RGB、深度、关节、位姿、里程计、IMU。
现在标准目录有 **107 个逻辑名**，覆盖既有 **21 类模态**。旧 38 项的名称和约束全部保留。
完整逐项列表见 [CHANNEL_INVENTORY](CHANNEL_INVENTORY.md)，机器可读目录由
[channel_registry.yaml](../../src/edc/data/channel_registry.yaml) 维护并随 wheel 安装。

| 类别 | 逻辑名称示例 | 声明以外的实际边界 |
|---|---|---|
| RGB、深度、红外、热像 | rgb/head、depth/wrist/left、infrared/main、thermal/main | RGB 已有图像/视频入口；其他模态须按格式、编码和字段单独验证，新增名字不安装解码器 |
| 机械臂、夹爪、灵巧手、腿、头、升降、躯干、底盘关节 | joint/arm/left/feedback、joint/hand/right/command、joint/leg/left/state | 已有 JointState/指令及显式数组读取；新设备的自由度、物理量和阈值仍需声明 |
| 位姿、速度、里程计 | pose/arm/right/feedback、pose/tracker/main、twist/base/command、odometry/base | ROS Pose 读取 position/orientation；Odometry 已补 pose/twist/两类协方差，独立 Twist 待补 |
| IMU | imu/main、imu/base、imu/wrist/left、imu/wrist/right | 已有 ROS Imu 到 ang_vel/lin_acc/orientation；数值物理检查要求单位、校准、语义和当前阈值确认 |
| 力／力矩、触觉、接触 | wrench/arm/left/feedback、tactile/hand/left、contact/foot/right | 可声明显式结构化字段；ROS WrenchStamped 规范字段解码及专用物理判据未补 |
| 点云、激光扫描、事件相机 | point_cloud/main、range_scan/base、event_camera/main | 命名已覆盖；对应 ROS payload 解释和专用质量算法分别待验收 |
| 音频、GNSS、设备状态 | audio/main、gnss/main、system_status/battery | 命名已覆盖；数值数组需显式映射，不代表音频解码、定位或电池诊断已完成 |
| 指令文本、标注、业务信息 | task_text/instruction、annotation/main、annotation/calibration、other/main | 命名已覆盖；字符串 payload 的业务结构和质量规则需要另行登记 |

命名、解码、算法是三个独立的验收项。Numeric_Check 可对已读取数值做有限性检查，不意味着每种模态有对应的物理检测器；时间戳检查也不证明内容质量或采集同步。
现实 topic 可任意命名，有限目录无法穷举所有厂商：标准类别 + @实例 + team__ 扩展共同覆盖声明需求。

## 三个配置位置

1. **框架词表**：src/edc/data/channel_registry.yaml。维护标准名及 modality/side/source_kind 等命名约束；不能在这里填写真实设备单位、时钟、标定或算法阈值。维护者修改后重新测试和打包；发布新版本时更新 version。
2. **机器人配置**：configs/robots/*.yaml。channels 描述字段、维度、单位、安装位置和角色；可用 channel_registry.ref 引用团队词表。源名称无论是 ROS topic、HDF5 路径还是 LeRobot feature，都沿用各自读取映射。
3. **数据集配置**：configs/datasets/*.yaml。source_bindings 把逻辑名绑定到当前批次实际源名；capture 在明确 scope 下声明批次的时间／单位／标定。未知信息保持 null/unknown，不从名字推断。

算法仍通过 channels 等既有参数选择逻辑名，运行时精确解析到源通道。同名多实例必须指定，例如 imu/main@primary；不会自动选择或组合，也不会改动时间轴。

## 参数文件示例

以下仅演示配置结构，不代表真实样例中存在这些源通道。

团队词表（已有标准名不必重复定义）：

```yaml
schema_version: edc_channel_registry_v1
version: 'lab-1'
channels:
  lab__tactile/palm:
    modality: tactile
    side: left
```

机器人声明：

```yaml
schema_version: 1
id: my_robot
version: '1'
channel_registry:
  ref: ../channel_registries/lab_example.yaml
channels:
  imu/main:
    logical_name: imu/main
    modality: imu
    fields: [ang_vel, lin_acc, orientation]
    time_source: unknown
    clock: null
    calibration_status: unknown
    field_metadata:
      ang_vel: {quantity: angular_velocity, dimensions: [3], unit: null}
      lin_acc: {quantity: acceleration, dimensions: [3], unit: null}
      orientation: {quantity: orientation, dimensions: [4], unit: null}
```

数据集绑定（确认录制里的真实名称后替换 null）：

```yaml
path: /path/to/recordings
source_bindings:
  imu/main: null
```

可直接修改的完整机器人例子见 [channel_registry_example.yaml](../../configs/robots/channel_registry_example.yaml)，
配套的 [数据绑定模板](../../configs/datasets/channel_registry_pending.yaml) 也包括触觉通道。
这两份是 opt-in 参数模板；尚无源数据绑定，null 会明确阻塞运行，不会把未录制的信号视为通过。
原有 [IMU 四配置入口](IMU_CONFIGURATION.md) 继续可用。

## 查看目录与实际覆盖

```bash
# 无数据输入：全部标准逻辑名
edc channels

# 读取某机器人引用的完整目录，或单独校验团队词表
edc channels --profile configs/robots/channel_registry_example.yaml
edc channels --registry configs/channel_registries/lab_example.yaml

# 有数据输入：逐 Episode 显示实际源通道、字段、可读性和逻辑绑定缺口
edc channels /path/to/data --profile /path/to/robot.yaml
```

Python 公共入口：

```python
import edc
registry = edc.load_channel_registry()  # 独立副本；也接受文件或 mapping
with edc.open_dataset('/path/to/data', profile='/path/to/robot.yaml') as dataset:
    for episode in dataset.iter_episodes():
        with episode.opened():
            report = edc.channel_catalog(episode)
            print(report['binding_coverage'])
```

binding_coverage 包含 source_channel_count、logical_channel_count、without_logical_name 和 missing_declared_logical_names；这是输入／绑定清单，不是质量通过率。
value_available/image_available 是读取器暴露的能力标记，不代替实际 payload 读取、算法执行或数据质量验收。

ref 相对于所在机器人文件解析，支持 YAML/JSON；导出的单文件配置冻结完整目录，原引用文件修改／移除不改变快照。run.json 也记录生效目录。重名、错误版本、标准名语义覆盖、冲突的左右／指令反馈及循环引用明确报错。物理量未确认不会因目录而自动补齐。
base_version 绑定已安装框架的标准词表版本；不同版本要求显式审阅迁移，不能悄悄改变历史解释。
未登记的 team__ 自定义名沿用旧兼容行为；需要语义校验时应登记到团队词表。

迁移边界：现有 logical_name、源名和算法参数兼容；未配置词表的直接 load_profile 不增加键。四配置解析和新运行证据增加目录快照。文件加载现在拒绝重复 YAML/JSON 键，旧文件若依赖后项覆盖前项须先合并；不支持用 YAML merge key 隐式覆盖声明。历史报告不改写。

## 当前真实样例的缺口

复用已核查的单个 X1 MCAP topic 清单：**32 路源 topic，23 路已有逻辑绑定，9 路仍未绑定**。该统计只代表指定文件，不代表磁盘中全部数据。

- 4 路：/left_arm/wrench_ext_local、/left_arm/wrench_ext_world、/right_arm/wrench_ext_local、/right_arm/wrench_ext_world。消息是 WrenchStamped；现 profile 为 other、fields=[]。下一步需解码 force/torque，分别登记 N 与 N*m、真实坐标系、反馈来源和校准，再补适用判据。同一路力传感器的 local/world 版本可用 @实例区分，但名称不证明坐标转换正确。
- 5 路：/diversity_data、/distribute_data、/instruction_data、/take_over_data、/calibration_data。消息是 String；需要先确认 payload 结构，再选择 task_text、annotation 或团队业务模态。不能从 topic 名替业务含义签字。
- /odom 已绑定 odometry/base；读取器已补六类字段，旧 X1 profile 仍为 fields=[]，须显式补声明启用，详见 [按构型配置](ROBOT_TOPOLOGY_CHANNELS.md)。
- 该样例没有 Imu 消息。本轮提供声明入口和待绑定模板，没有给真实样例添加虚构 IMU。

完整源名、消息类型、数量和现有绑定见 [CHANNEL_INVENTORY](CHANNEL_INVENTORY.md)。目录配置化之后已增加 [Odometry 与构型配置](ROBOT_TOPOLOGY_CHANNELS.md)；其余 payload 解码及专用物理算法仍是独立缺口。
