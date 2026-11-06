# 内置质量算法参考

[返回主页](../../README.zh-CN.md) · [English](ALGORITHMS.md) · [算法运行](../how-to/RUN_AND_SELECT.zh-CN.md)

本页逐项说明九项内置质量算法的**输入条件、参数、输出与判断、适用边界**，用于查询，不包含运行步骤。
如何确认输入、选择算法、执行并阅读报告，见[算法运行](../how-to/RUN_AND_SELECT.zh-CN.md)；输出对象的字段见[接口与输出参考](OUTPUTS.zh-CN.md)。

所有参数以安装版算法元数据及源码为准，示例参数不作为跨设备通用标定。共同结果表示为通过、复核、可救、排除；没有检查条件和执行失败与质量判断分开。

<a id="structure-check"></a>
## Structure_Check · 结构检查

- **输入：** 通道目录、已声明相机要求、时长与机器人部件。
- **参数：** 卡片 `data.min_duration_s` 默认3秒；缺失要求由配置决定。
- **输出／判断：** 结构评分、缺失项与时长问题；缺少要求内容或过短会影响结论，过短轨迹可阻止其余检查。
- **边界：** 检查文件结构不证明物理语义、任务成功或传感器健康；缺 `camera_info` 与缺相机不是同一条件。
- [实现](../../src/edc/algorithms/quality/algorithms/Structure_Check/)

<a id="numeric-check"></a>
## Numeric_Check · 数值检查

- **输入：** 已登记数值；物理子项另需确认语义、SI 单位、标定和对应阈值。
- **参数：** `freeze_min_duration_s` 默认5；卡片可提供 `range`；关节限位和速度上限只读 URDF，没写 URDF 就不查并在报告中写明；`joint_stuck` 需要同臂关节和末端反馈。
- **输出／判断：** 逐字段有限值、超限与冻结事件；NaN／Inf 或明确超限可排除，冻结线索需复核；关节卡住且末端运动可给出可救项及处理建议。
- **边界：** 未配置的物理子项不算检查通过。IMU 等已接入子检查属于本算法；未知 effort 不解释为力矩，不把指令当反馈。
- [实现](../../src/edc/algorithms/quality/algorithms/Numeric_Check/)

<a id="action-smoothness"></a>
## Action_Smoothness · 动作平滑度

- **输入：** 所选臂的末端 XYZ 反馈，单位m；记录值或显式 URDF 正运动学结果，以及该通道可用时间。
- **方法：** 按通道自身时间分段，用各段中位间隔建立等间隔网格；频域提取保持段振动加速度。没有默认将头部相机作为参考，也不使用固定帧数的 jerk 方法。
- **参数：** `band_hz: [2, 10]`、`band_rate_ratio: 0.4`、`min_rate_hz: 10`；`still_speed_m_s: 0.01`、`still_min_s: 0.3`；`still_review_m_s2: 1.0`。
- **输出／判断：** 各臂保持段 RMS 加速度（m/s²）、事件和冻结线索；只有通过、复核、不适用。运动段数值供参考，不用于排除。
- **边界：** 频段上限受采样率限制；复核线仍需目标场景标定。缺时钟单位、采样不足或无保持段时不能声称完成振动检查。
- [方法与全部参数](../../src/edc/algorithms/quality/algorithms/Action_Smoothness/README.md)

<a id="check-timestamp"></a>
## Check_Timestamp · 时间戳检查

- **输入：** 有效且单位明确的时间；跨通道配对另需可比较时间和参考输入。
- **参数：** `shared_row_max_gap_s` 默认按中位行间隔的3倍；评分标准所有机型统一：误差下限取最快彩色相机帧间隔的 1/3，帧间隔波动系数 0.3 起扣分、0.6 扣到 0。
- **输出／判断：** 时间间隔、断档和可用配对指标；可只检查单路／共享行时间，不把该范围报告为跨设备同步通过。每路和参考相机的录制时间段（按采集时间）重合低于 95% 时，每少 1 个百分点扣 2 分；完全不重合才记 0 分。参考相机（头部）不和自己比，只按同样的节奏规则单独打一行分，低于其他路的平均分时才计入平均（只扣分、不加分）；它时间倒退、重复或算不出采样周期时不能当基准，整项记 0 分（排除）。
- **边界：** 缺独立采集时间、单位或 clock 时，推荐列明部分可检查范围。
- [实现](../../src/edc/algorithms/quality/algorithms/Check_Timestamp/)

<a id="sync-check"></a>
## Sync_Check · 采集同步

- **输入：** 多路独立、有效的采集时间，满足共同 clock 或显式映射条件；ROS/MCAP 另用每条消息的录包时间。拆出的通道和源话题同一条时间轴，只算一次。
- **参数：** 没有可调参数（不再有 `primary`、`weights`）。
- **输出／判断：** 两项，没有分数。① 起止和交集（按录包时间，没有录包时间才按采集时间）：共同时段低于全程 80% 排除，低于 95% 或某路晚开始／早结束超过 5 秒复核，并点名差几秒。② 各路时钟（每路自己的“录包时间 − 采集时间”）：各路差出 1 秒以上为时钟没对准，固定偏差可救、偏差在变排除；同一路在数据中变化超过 100 毫秒复核（漂移或跳变）。没有录包时间时第②项没评估完。
- **边界：** 共用一条行时间不能验证多设备采集同步；录包时间含传输延迟，1 秒、100 毫秒以下的差别不当作时钟问题。跨路逐帧对齐由 Check_Timestamp 负责，这里不再重复。
- [实现](../../src/edc/algorithms/quality/algorithms/Sync_Check/)

<a id="root-cause"></a>
## Root_Cause · 时间异常归因

- **输入：** 可比较的多路时间及来源分组；时间条件与跨通道检查一致。
- **参数：** `families` 描述哪些通道属于同组来源。
- **输出／判断：** 三项，写明第几秒到第几秒。① 多路同时中断（100 毫秒内至少 3 个来源一起断，有一路断 0.5 秒以上）：整机卡顿，排除；② 单路断档（只有一路断 0.5 秒以上，其他来源没有一起断）：那一路设备的问题，复核；③ 周期性丢帧：排除。时钟漂移／跳变不在这里查，由 Sync_Check 按每路“录包时间 − 采集时间”查（2026-11-04）。
- **边界：** 这是定位线索，不是设备故障的确定诊断；单一共享行时间不足以独立归因。
- [实现](../../src/edc/algorithms/quality/algorithms/Root_Cause/)

三个时间算法的可选 `record_header_deviation_threshold_ms` 与原时间差证据见[范围与证据参考](EVIDENCE_AND_SCOPE.md)。默认 `null`；补充证据不自动改分。

<a id="dedup-check"></a>
## Dedup_Check · 时间／序列重复

- **输入：** 原始时间值或支持的序列信息；共享时间仅检查一次并保留来源。
- **参数：** 按已选通道与算法声明执行，不把图像重复参数传给本方法。
- **输出／判断：** 重复计数、连续重复片段和已有重复评分。
- **边界：** 时间相同不证明图像或动作内容复制；缺时间的通道不能静默算作已检查。
- [实现](../../src/edc/algorithms/quality/algorithms/Dedup_Check/)

<a id="reset-detection"></a>
## Reset_Detection · 复位检测

- **输入：** 所选臂反馈位置及同机器人、同字段的 Home 与正容差。
- **参数：** 卡片 `home` 可使用关节rad或末端m；双臂须保持同类型和相同卡片容差。完整 profile 也支持明确的逐通道标定。`home` 只填采集团队确认过的姿态；给不出或数据是剪出来的片段时，用 `checks.skip: [Reset_Detection]` 跳过。
- **输出／判断：** 开头／结尾与参考的偏差及复位判断，超出条件需复核。
- **边界：** 关节数、单位、零位或坐标系不匹配时，不能套用另一机器人的 Home；任务允许不同初末状态时需调整使用范围。
- [实现](../../src/edc/algorithms/quality/algorithms/Reset_Detection/)

<a id="video-integrity"></a>
## Video_Integrity · 图像／视频完整性

- **输入：** 所选腕部及明确共享相机的可解码帧／视频。
- **参数：** `input_mode` 为 `auto`／`frames`／`video`；`sample_step` 默认5，`downscale` 控制检查尺度；`freeze_motion_review` 默认空映射。
- **输出／判断：** 黑白屏、模糊、异常画面、冻结等候选与计数；当前画面规则中某类问题占抽检帧30%及以上可排除，低比例仍可通过并附数量。评分与人工复核线索可以并存。
- **边界：** 图片没有容器指标；抽样结果不等于逐帧结果。冻结联合复核需要已配置反馈及共同 clock／显式映射，只补证据，不自动重判。[联合复核参数](EVIDENCE_AND_SCOPE.md)
- [实现](../../src/edc/algorithms/quality/algorithms/Video_Integrity/)

## 相关页面

- [算法运行](../how-to/RUN_AND_SELECT.zh-CN.md)：预检、执行与报告阅读。
- [接口与输出参考](OUTPUTS.zh-CN.md)：`Item`、`AlgorithmResult` 与结果记录。
- [格式与配置参考](DATA_AND_ROBOTS.zh-CN.md)：各算法依赖的字段、单位与时间声明。
- [范围与证据参考](EVIDENCE_AND_SCOPE.md)：机械臂范围、时间差证据、画面联合复核。
