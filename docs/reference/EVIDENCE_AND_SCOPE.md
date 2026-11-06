# 范围与证据参考

[English](EVIDENCE_AND_SCOPE.en.md)

本页用于**查阅**机械臂范围、时间差证据与画面联合复核的配置项和证据字段；不新增独立算法、不改变算法评分、物理阈值或输出基类。
算法本身的输入与判断见[内置质量算法参考](ALGORITHMS.zh-CN.md)。

## 时间算法：用户配置与结果

在机器人 YAML 的 `checks` 下填写；三个方法共用相同参数，也可在工作流阶段 `params` 中覆盖：

```yaml
checks:
  Check_Timestamp:
    record_header_deviation_threshold_ms: null
  Sync_Check:
    record_header_deviation_threshold_ms: 100
  Root_Cause:
    record_header_deviation_threshold_ms: null
```

- 默认 `null`：显示中位差、最小／最大差、最大绝对偏离及对应消息，不设置新的异常阈值。
- 显式非负毫秒值：额外列出 `abs(偏离量) > 阈值` 的数量、连续源行时间段与明细；明细最多20条，`details_truncated` 表示是否截断。
- 实际差值＝原始记录时间−原始 header；偏离量＝实际差值−全段中位差。先对整数纳秒相减，之后转换为毫秒，使用时钟映射或自动平移之前的原值。
- 原始一行对应一条消息，`source_row` 从0开始，`message_number` 从1开始。派生视图不重复计算同一原消息序列。缺失／零 header 对不参与统计，报告无效数量；缺少某类原始时间时标为不可计算，不借用其它时间。
- 差值是观测事实，不据此推断设备故障或网络原因。本证据不创建自动扣分、复核状态或新的放行条件。

JSON 的 `record_header_differences` 保留完整统计，并通过既有 `Item.evidence` 保存逐条证据。例如：

> /arm 第151条：时间差500 ms（通常20 ms，偏离+480 ms）

批量 HTML 沿用原分支四个栏目，算法格子保留本项结论与自身简短说明，非通过项显示原有原因。
不新增“已检查明细”、检查范围说明或独立时间差表；缺少配对时间也不生成重复的“无法计算”表格。
中位值、差值范围、源行、阈值、超限消息和完整输入范围保存在 JSON 中。渲染不会修改这些记录，补充时间证据也不改写评分或质量判断。
原报告标签及判断规则保持；不增加“未判定”或“未完成评估”等展示标签，不人为加入失败样例。实际执行错误仍保留原因。

本轮受控 ROS2 MCAP 对照中，正常与单条尖峰的原评分分别都为时间戳99.844、同步100、归因100。
这些数字仅说明新增证据未改变该合成输入的评分，不代表真实机器人验证或性能指标。
固定2 s差值继续由已有逻辑报告；新增偏离量为0，不把稳定偏差冒充局部尖峰。
[范围结果样例](../examples/arm_scope_after.json)。

## 机械臂范围：数据侧声明，算法侧选择

机器人 YAML 的 `arms` 声明实际机械臂及各字段来源；算法的 `checks.<方法>.arms` 选择本次范围。
独立单臂用 `main`，双臂默认 `all`；`[left]`／`[right]` 按已声明部件 ID 选择，不根据 topic 拼写猜测。

```yaml
arms:
  left:
    joints: {source: combined_joints, index: 0-5, unit: rad}
  right:
    joints: {source: combined_joints, index: 6-11, unit: rad}
checks:
  Numeric_Check:
    arms: [left]
```

本例只检查前6个关节，后6个的 NaN 不混入左臂结果。左右分存时填写各自 `source`，行为相同。
显式选择共享原数组时仍展开已声明的分量视图；覆盖过的原字段不作为整个数组重复检查。
部分原字段尚未被视图覆盖时，仅保留归属明确的未覆盖字段诊断；只选一条臂时，共享原字段未声明分量则不检查并提示补充。归属不明的原始数值输入不会自动视为共享传感器。
部件关系由配置及 `select` 来源关联确定，夹爪、手和末端姿态也随所属臂筛选；不将夹爪维数当作机械臂关节数。

推荐与执行共用 `input_scope`：`channels`、`channels_by_arm`、`shared_channels`、`excluded_inputs`、
`missing_ownership`。未选侧和被分量视图覆盖的原字段记为 `not_checked`，不是检查通过。
未知归属会使范围不完整。报告继续逐字段保存实际通道、部件与左右证据，不生成跨臂总分。

## 视频算法：可选联合证据

```yaml
checks:
  Video_Integrity:
    input_mode: auto
    freeze_motion_review:
      rgb/head:
        - pose/arm/left/feedback
        - pose/arm/right/feedback
```

`freeze_motion_review` 默认 `{}`；相机键与运动通道可使用逻辑名或精确源名。
这是算法侧的可选输入绑定；相机角色、反馈语义、单位、部件和 clock 来自机器人／数据配置。
首版仅支持已确认的关节或末端 `position` 反馈，通过现有 SI 接口读取。
运动通道仍受本次 `arms` 限制；缺失可选通道不阻止原画面检查。

复用现有灰度哈希冻结候选，不改变重复画面检测。每个可关联区间附带：

- 原画面区间与最多3张图片样例（开始、中间、结束），不生成预览视频。
- SI 位置曲线 SVG、源行号、时间、单位和运动范围；大区间曲线最多约1001点，明确记录抽样步长与原样本数量。
- 相机 clock、时间来源、明确的时钟映射和运动信号来源／分量选择。

`frames` 与 `video` 两个入口都支持。若显式选 `video`，其原有视频物化行为保持；本联合证据只增存 PNG／SVG／JSON。
关联要求两路具备可比较的记录时间来源及共同 clock，或配置中存在显式映射；仅选头部相机不证明同步。
缺少时间、单位或运动条件时，`freeze_motion_review.status: unavailable` 与原因保留，原画面得分和结论不变。
无候选为 `no_candidates`。部分通道可关联时，每条运动输入独立记录状态。
静止画面与静止机器人可以一致；机器人运动也可能发生在视野外，因此这些证据只供人工复核，不自动认定复制帧或故障。
