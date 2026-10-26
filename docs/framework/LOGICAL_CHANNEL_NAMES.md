# 统一通道名称与源字段绑定

算法可以选择多个 channels。现在既接受精确源名称，也接受机器人配置中的
logical_name；两种方式最终指向同一个读取通道，不重新编码、不改原始文件。

## 三层名称

| 层级 | 例子 | 作用 |
|---|---|---|
| 统一逻辑名称 | rgb/chest | 算法输入和用户查看 |
| 源 Channel / ROS topic | /camera_chest_front/color/image_raw/compressed | 原读取器寻址与证据定位 |
| LeRobot feature | 取实际 feature_mapping 声明，不从逻辑名称猜测 | 数据存储与官方读取 |

统一名称是本框架的配置约定，不宣称是 LeRobot 官方 topic 标准。
HDF5、MCAP 和 LeRobot 共用这层映射，改名称不要求转换整个数据集。
LeRobot 官方包装通过既有 lerobot.feature_mapping 定位 feature；
logical_name 放在同一 channels 配置中，不重复登记单位、关节和角色。

## 用户配置一次，算法复用

在现有 robot.yaml 的对应通道下增加一行，保留其他声明：

```yaml
channels:
  /camera_chest_front/color/image_raw/compressed:
    logical_name: rgb/chest
    modality: rgb
    body_part: chest
    sensor_id: chest_front
    representation:
      camera_role: chest
      camera_role_status: confirmed
```

算法配置使用统一名称，例如：

```yaml
Video_Integrity:
  channels: [rgb/head, rgb/chest, rgb/wrist/left, rgb/wrist/right]
  input_mode: auto
  sample_step: 1
  downscale: 4
```

更换机器人时，把相应实际 topic 绑定到同一个 logical_name，算法配置继续使用。
现有 channels 源名与 Visual_Quality cameras 语义选择保持兼容。
Visual_Quality 胸前评分档仍未支持，rgb/chest 不会变成 wrist。

初始通用目录由 src/edc/channel_names.py 维护：RGB/深度相机、双臂关节与末端，
反馈和指令分别命名。自定义使用 team__ 前缀，例如 lab__tactile/left。
同部位多设备使用实例名，例如 rgb/chest@front 和 rgb/chest@rear；
请求 rgb/chest 不会自动挑其中一路。映射必须是精确源名，不能用通配符绑定。

补充目录支持用户注册的设备类别，不通过 topic 中的单词判断设备：

| 统一名称 | 配置含义 |
|---|---|
| joint/gripper/left/state、joint/gripper/right/state | 已注册夹爪的关节通道；state 不代表反馈角色已确认 |
| joint/gripper/{left,right}/{feedback,command} | 已确认反馈或指令的夹爪通道 |
| joint/head/state | 头部关节；倾角、伸缩等物理量在各字段单独登记 |
| joint/lift/state | 升降关节；位置与速度、effort 分别登记物理量及单位 |
| odometry/base | 基座里程计类别；具体 pose/twist 字段仍需读取映射 |

`representation.device_type: gripper` 是用户注册的设备证据。
已登记为 dexterous_hand 的通道不能绑定 gripper 逻辑名；未知设备类型不会由名称自动补齐。
主端的 command 描述已确认的数据角色，不证明与从端命令一一对应或已同步。
新增目录只改变配置端的寻址与冲突校验，算法仍按原有 channels/field 取数。

数值通道名称与字段分开：pose/arm/right/feedback 指向末端反馈通道，
position 和 orientation 继续作为字段，单位和坐标系分别保留。
一个源数组包含左右手或不同物理量时，先使用现有 select 配置显式选择字段/分量，
再给选出的通道登记逻辑名称，不从数字或索引推测左右。

## 目录、推荐池与执行证据

```python
import edc
with edc.open_dataset(source, profile=robot_yaml) as dataset:
    episode = next(dataset.iter_episodes())
    print(edc.channel_catalog(episode))
    print(edc.resolve_channel(episode, 'rgb/chest'))
```

目录显示统一名称、真实通道、实际 LeRobot feature 的 source_mapping、fields、
单位、角色、时钟、条数及原文件，并显示逐通道 image_storage（frames/video/unknown；非图像为 null）。
公开函数由 edc 薄导出，实现归 framework。

9 项 CPU 适配器支持 channels/channel/primary/families 的逻辑名称，保存请求名与实际绑定。外部插件在 data_requirements 声明 channel_param 时也由框架共享解析；其它自定义寻址应声明契约或显式调用 edc.resolve_channel，不能假定任意字符串自动解析。

配置归属见 [公共 README](../../README.zh-CN.md#数据使用者如何配置)，开发者契约见 [ONBOARDING](ONBOARDING.md)。

## 校验边界

- 重名、源名遮蔽、解析后重复选择、已知左右/模态/角色冲突均明确拒绝。
- 缺右腕不回退左腕；缺设备不选第一路；未知单位/clock/反馈语义不自动确认。
- logical_name 是寻址引用，名字里的 feedback 不代替 source_kind 的确认。
- 算法适用性仍需原有单位、角色、标定、时间轴和模型依赖。
- 原算法方法、评分、阈值不变；逻辑名初次适配版本 CPU ir-adapter-10、视觉/投影 ir-adapter-4；
  本库 CPU 适配统一为 ir-adapter-14，实际方法保持原样；GPU 插件不在本库范围。
- 已记录的配置与输出不原地改写；转换输出按既有流程保留源映射和独立验收。


## 本库验收

逻辑名／原名、SI 与显式时钟映射的契约测试见 tests/edc；版本和实测结果见 [STATUS](STATUS.md)。迁移前真实 X1 报告未随库分发。
