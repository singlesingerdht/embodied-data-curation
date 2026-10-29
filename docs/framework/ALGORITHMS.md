# CPU 方法、输入与输出

基础包内置以下九项。源代码位于 `src/edc/algorithms/quality/algorithms/<ID>`；输入契约与适配位于 `src/edc/algorithms/quality/adapters/{contracts,cpu,selection}.py`，推荐位于 `src/edc/algorithms/recommendations.py`。方法计算、评分和阈值保持原定义。

所有 CPU 方法通过机器人配置解析 `arms: all`；头部、胸前、升降等共享输入按所选方法保留。单臂不要求不存在的另一侧；声明双臂而缺一侧不能完整通过。显式 channels 可缩小范围，报告与 gate 会保留范围及缺失状态。

仓库还登记了 57 个按模态展开的质量指标，使用 `quality_indicators` 预设显式选择。它们与下面九项共享 Dataset → Episode → Channel、逻辑通道、SI、时间来源和 `dqs_record_v1` 契约，全部由本仓库实现或登记，不需要单独安装算法包。指标清单位于 [`inventory.json`](../../src/edc/algorithms/quality/algorithms/Indicator_Checks/inventory.json)；`implemented: false` 的项目会在推荐池中说明“当前版本未实现／缺少输入”，不会被执行为通过。

指标池同时声明配置端输入契约：55 项通过 `data_requirements` 声明模态、字段、所需 SI 单位、语义角色、标定、时间来源、最少样本及最低通道数；M8/M9 分别通过 `required_inputs` 声明文件和 TF 资源。同步指标还需自有预检确认独立采集时间轴及共同时钟；声明覆盖不代表所有指标已实现或在当前数据上可用。

**使用者端**在算法配置中填写 `channels`，可列出多个逻辑通道；不填时由指标预检按模态选择默认范围，需求检查显示 `deferred`，实际范围仍写入输入绑定。`required_channels` 和 `field_map` 也支持逻辑名，推荐与执行共享实际源绑定。**算法开发者端**使用同一契约，`channel_param` 可引用字符串或字符串数组参数；未提供默认范围预检的插件必须显式指定输入。图像、位姿和同步相关的可选运行依赖在推荐端列出，基础安装不强制安装它们。

指标输出保留原方法的事件、明细和来源证据，`score/passed/verdict` 均为 `null`、`assessment_complete=false`；执行完成与数据达标分别报告。配置及执行回归见 [指标契约验收](../validation/indicator_contracts_20261029/README.md)。

```bash
edc algorithms                         # 显示 9 个默认 CPU 方法和 57 个内部指标
edc run DATASET --preset quality_cpu   # 只运行稳定的九项默认方法
edc run DATASET --preset quality_indicators  # 运行已满足输入条件的内部指标
```

| ID | 最低输入／默认范围 | 输出与边界 |
|---|---|---|
| Structure_Check | 所选臂关联通道与共享目录；相机要求来自 robot.camera_requirements | 结构评分、缺失通道、相机配置证据；不验证单位或采集同步 |
| Numeric_Check | 所选臂关节、末端、工具及共享已登记数值字段；保留指令／反馈区别 | NaN/±Inf 的字段与位置；物理子项分别列可用／未配置／待确认。有限值使用原值；物理输入用 SI，不能把“没有非有限值”当成物理合格 |
| Action_Smoothness | 每个所选臂的 `pose/arm/<id>/feedback.position`，米制和有效逐行时间 | 每臂独立平滑度结果、静止段与原因；显式选 topic 时禁止另一侧或 command 回退 |
| Check_Timestamp | 所选采样通道各自的有效记录时间轴，单位明确；跨路另需独立轴和共同 clock | 各路间隔、回退、频率及有条件的跨路结果。元数据／事件不按采样流评分；通道低频不直接视为缺帧 |
| Sync_Check | 至少两条独立 acquisition 轴和共同 clock／明确映射；可为单臂＋头相机 | 时间差、覆盖和稳定性；primary 可选头相机；不能用同一合成行时间证明采集同步 |
| Root_Cause | 所选通道的可比较时间轴、共同 clock、可选 families 分组 | 时间异常来源、关联证据与归因限制，不是硬件故障的确定诊断 |
| Dedup_Check | 所选采样通道的原始记录时间／显式原时间字段，可选序列号 | 重复位置与数量、原评分。只做时间／序列重复；静止画面、图像复制与非相邻内容重复不属于该项 |
| Reset_Detection | 所选臂的显式 Home 标定通道；当前 X1 Pro 使用左右末端 feedback.position 的三维 `m` 标定，也兼容 joint feedback 的单一 `reference/tolerance` | 按坐标分量输出首／末帧误差和双阈值分数；未确认的 Home、单位、维度或关节顺序保持 `needs_calibration`，不自动补姿态 |
| Video_Integrity | 所选腕 RGB＋配置要求的头部／胸前 RGB，frames 或 video | 画面异常、原视频容器证据与复核线索分别记录。auto 尊重存储；frames 不生成 MP4，容器子项不适用；video 物化需要实际时间或显式 FPS |

## X1 Pro 的实际映射

唯一完整配置是 [`configs/robots/x1_pro.yaml`](../../configs/robots/x1_pro.yaml)。32 路源 topic 保留，23 个逻辑名；不同信号不合并。用户确认如下：

- 四路 RGB：camera1 左腕、camera3 右腕、camera_head_front 头部、camera_chest_front 胸前；头部深度登记 `depth/head`，深度单位／比例／无效规则未知。
- 左右 joint_states 为 feedback；master 双臂关节和 master gripper 为 command。普通 gripper 已确认设备类别，具体 command／feedback 仍未知，逻辑名使用 state。
- 六路 end_pose XYZ 均为 m；普通双臂及 whole_body 双臂为 feedback，master 双臂为 command。whole_body 保留 `@whole_body` 名称，平滑度默认选无后缀的每臂反馈。
- 头部 position 为倾角，lift.position 为直线量，单位未知；odom 归里程计，具体数值字段待登记。
- 四路 wrench 与 tracked_pose 未确认部分保持未知。五路文本／事件元数据保留原始读取，不纳入采样质量评分。
- 关节位置等未确认单位不猜填；六路 XYZ 的语义登记不证明其他物理子项、标定、作者阈值确认或共时钟已完成。右 master 末端零 header 不能用于采集间隔检查。

## 可验证依据

- 迁移前后的九项 CPU 同输入、同参数对照记录见 [EDC 状态](STATUS.md) 及本轮验证目录。
- 单臂／双臂、缺侧、原值有限性、同机器人两任务、配置导出与方法子集测试：`tests/edc/test_unified_curation.py`。
- 单位、时间来源、显式时钟映射和左右边界分别见 `tests/edc/test_unit_normalization.py`、`test_workflow_inputs.py`、`test_explicit_input_bindings.py`。
- SDK 盘点、亮度编辑与增强仅验证公共契约；真实价值和处理收益仍待逐项接入。
