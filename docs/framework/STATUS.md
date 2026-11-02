# 当前验收状态

日期：**2026-11-02**。框架版本 **0.2.0a2**；Numeric_Check 适配 **ir-adapter-22**，Action_Smoothness 适配 **ir-adapter-24**，Reset_Detection 适配 **ir-adapter-17**，Sync_Check 适配 **ir-adapter-19**，Root_Cause 适配 **ir-adapter-20**，Check_Timestamp 适配 **ir-adapter-19**，Video_Integrity 适配 **ir-adapter-16**，其余 CPU 适配 **ir-adapter-15**。

当前算法池为 **66 个仓库内登记项**：`quality_cpu` 默认预设 9 个 CPU 方法，`quality_indicators` 预设 57 个模态指标，其中 46 个已实现、11 个登记但因实现或输入条件暂不可执行。全部算法均由本仓库实现或登记；基础安装不依赖外部算法包、模型、私有 wheel 或 GPU 运行时。单独安装的集成只属于开发者接口，不改变这组核心数量。

本次 `data-platform` 基线为 `leju-kuavo` 的 `87c5e6f`，使用独立工作区，未带入其他工作区未提交修改。Quickstart 首轮源码全量 **846 passed / 23 skipped**；相关契约专项 **112 passed**。后续报告统一变更的验证范围单独记录如下，不将首轮全量结果当作新变更的全量验收。

## 按用户要求恢复 KUAVO 报告结论（2026-11-02）

- 页面仅使用原有通过／复核／可救／排除及不适用，不显示“参考”或“未评估完”。缺输入、无质量判定的项显示不适用，原因保留在原版逐条详情；错误不变成通过，未完成的评估不能显示建议保留。内部 `dqs_record_v1`、完整性和工作流状态不改协议，历史记录不改写。
- 撤回容器时间包装层的判定覆盖：显式选择记录时间的动作平滑度、同步及归因使用原方法评分和结论，不因时间来源直接变成参考／复核。设备采集时钟仍未知，不据此证明采集同步。频率标定、非法轴、缺口和外推边界继续检查；平滑度插值、数值阈值及原算法未改变。
- README 原命令重新执行真实样例：九项共 **10 条记录全部 completed**；双臂平滑度各 **100 分／通过**，同步 **99.9 分／通过**，归因按原方法 **0 分／排除**；时间戳对齐不适用，数值及复位复核。整体显示含建议排除项，交付仍挂起。补充 batch 也重新执行；五处 HTML 的 CSS、四个主体标题及四项图例均与参考附件一致，链接有效，无新增结论标签。
- 源码专项 **91 passed**；源码外安装 wheel 专项 **90 passed / 1 deselected**，仅依赖源码布局的 `--robot` 配置快捷名测试在源码执行。wheel／sdist 构建、干净基础安装与 `pip check` 通过；wheel **211 个 Python 文件**与源码一致，基础安装发现 66 项且不加载格式／GPU／模型库。未重跑仓库全量。原始 MCAP SHA256 不变，**328 个历史运行／配置文件**哈希不变。详见[验收记录](../validation/kuavo_report_restore_20261102/README.md)。下方同日各节保留历史结果，当前行为以本节为准。

## MCAP 记录时间分析与参考样式恢复（2026-11-02，历史阶段）

- `Sync_Check`／`Root_Cause` 新增显式 `timeline: {time_source: container}`，推荐与执行使用同一范围。复用现有同步及 gap 关联方法，使用同一 MCAP 的 `log_time`／记录 clock；不平移时间，不更改设备 clock，不回退到采集或合成时间。原方法评分保留为 `method_score`，正式采集判断保持未评估。默认旧配置行为保持兼容。
- 原始容器纳秒、秒制时间和源行映射写入各算法 `artifacts/<id>/container_timeline.json`。少样本、倒退或未知 clock 的通道保留缺口原因；不同记录 clock 拒绝比较。记录断档及相对时序变化可能包含传输、排队和发布影响，不证明采集丢帧、设备时钟漂移或硬件故障。
- 真实 Quickstart 与 batch 均重新执行：九项、双臂共 **10 条记录全部 completed**；两项记录时间诊断覆盖 **27 路**，无不可用采样路。同步记录覆盖率 **99.8%**，相对头部参考的最近邻间隔 P95 最大 **49.39 ms**；归因记录 **53 次关联间隔异常**，最大记录间隔 **0.677 s**。这是记录端证据，源命名空间分组不证明独立硬件。工作流处理状态变为 `completed`，交付仍 `suspended`、轨迹 `unassessed`，退出码 1 不表示处理错误。
- 按用户要求撤回额外报告面板：仅保留参考样例的四种结论说明和四个主体栏目，单条仍用 batch 表格一行。CSS 与附件 `x1pro_sample50.html` **逐字一致**，四个标题及四项图例一致；完整原始结果、历史和配置留在 catalog／`_runs`，页面不嵌入技术报告、配置面板、工作流或历史。当前主报告 **23,815 字节**；统一／batch／batch 保留结果三处均已核对样式及链接。
- 真实源 SHA256 前后均为 `95993aab130d1ffa2ea9cb9367911f8216f90b2268b076136908f7130053b6d2`。本次主运行 **39 个证据文件**存在；安装后的基础 wheel 刷新主报告，**222 个记录／配置文件**哈希不变。
- 源码相关检查分批 **29、137、26 passed**（集合有重叠，不相加）；源码目录外 wheel 契约 **45 passed**。wheel／sdist 构建成功，wheel 的 **211 个 Python 文件**与当前源码一致。隔离基础安装及 `pip check` 通过，可发现 66 项且不导入格式／模型／GPU 库；安装环境检查排除宿主 ROS `PYTHONPATH`，不将 ROS 环境缺失 setuptools 视为本包失败。本轮未重跑仓库全量测试。
- 当前验证及命令见 [记录时间与参考报告](../validation/recording_time_20261102/README.md)。以下两节保留本日早先方案及首轮结果，当前行为以上述记录为准。

## 单条与批量共用质检报告（2026-11-02）

- `edc run`、`edc config run` 的 `report.html` 与 `edc batch` 共用 batch 模板，单条轨迹显示一行。算法明细、原始评分、源 selector、证据链接在同页展开；配置确认与字段范围折叠展示，历史运行分别保留。原详细渲染入口仅作兼容转发，不再维护另一套页面模板。
- catalog 及运行快照提供轨迹与所选算法范围，不按记录数或源文件数推断轨迹；dataset 级结果单独展示，不当作逐轨迹质量通过。缺输入、部分覆盖、失败与参考项保留，过期的 keep 记录不能覆盖未完成状态。大型原始 JSON 通过不可变 JSONL 链接按需查看，不再重复嵌入页面。
- 已有输出可用 `edc report <输出目录>` 刷新页面，无需重跑算法。真实 Quickstart 页面从 **47,453,528** 降至 **1,935,946 字节**（含三次历史运行），本次安装 wheel 在源码目录外刷新两处结果，**239 个运行／工作流／catalog 文件**哈希不变，**207 条相对文件链接**存在，轨迹锚点唯一。原 MCAP SHA256 不变；整体仍为 `unassessed`，同步及跨源归因仍未评估。
- 源码相关测试 **101 passed**，包括报告、批入口、工作流、统一配置及 MCAP 示例；源码外 wheel 报告契约 **16 passed**。wheel/sdist 构建、干净基础环境安装、`pip check` 通过，wheel 的 **210 个 Python 文件**与源码一致；基础报告不加载格式、模型或 GPU 依赖。本轮未重跑全量算法测试或真实算法计算。
- 验证记录：[报告统一](../validation/report_unification_20261102/README.md)。中英文 Quickstart 已同步；输出路径和原始记录协议不变，迁移说明见 [配置与报告](CONFIGURATION.md#质检报告与已有结果迁移)。

## X1 Pro MCAP Quickstart（2026-11-02）

- `configs/x1_pro.yaml` 引用 MCAP 数据集、机器人、算法与显式九项工作流。真实文件由数据集索引登记为 `x1_pro_reference_0001` 一条 Episode，保留 32 路源通道；任务未知，不按目录推断。中文／英文 README 以此为主例，补充明确 MCAP 路径的 `edc batch`，两入口使用相同机器人和算法参数。
- 新增 `Episode.time_axis(channel, *, time_source)` 和公共 `edc.time_axis`，返回 `TransformResult`，保留 acquisition/container 的秒制时间、clock、源行及原始纳秒。`align_signal` 可显式选择来源，未选择时保留旧行为。未知共钟不能进入采集比较；零 header 不回退。
- 动作平滑度左右臂分别以反馈 XYZ 插值至头部 RGB 的真实 `log_time`，只用重叠帧，不外推，源插值跨度上限 0.1 秒；保存源行、权重及参考行。实测左臂 **1,434 帧 / 3.404470 m/s³**、右臂 **1,435 帧 / 4.113187 m/s³**，均为容器时间诊断，`score=null`、`NEEDS_REVIEW`、`assessment_complete=false`。原始采集轴不变，不能据此认定采集同步或机械振动质量通过。
- 用户确认主臂为指令、从臂为反馈，六轴顺序、rad/rad/s 及适用标定；主臂速度求导不一致诊断保留。机械臂 effort 为电流、单位未知，不作为力矩。Numeric 物理范围固定为四路臂 position/velocity 和两路反馈 XYZ，共 **10/44 字段**；反馈／指令限位分表登记，沿用用户确认的厂家限位及速度上限，摘要绑定范围与阈值，标识 `data-platform-owner`。其他字段只查有限值；旧配置未填范围时保持旧契约。
- 真实统一入口与 batch 均生成九项的状态记录（双臂动作共 **10 条记录**），没有 `processing_error`；Sync_Check／Root_Cause 因共同采集时钟未确认而为 `missing_input`。Reset 保留 MCAP v2 标定，结果 `NEEDS_REVIEW`；四路 RGB 直接检查 **1,449 / 1,454 / 1,435 / 1,449 原帧**，未转 MP4。工作流 `status=blocked`、`delivery_status=suspended`、轨迹 `unassessed`，CLI 退出码 1 是未满足放行条件。batch 同样为 `unassessed`。
- 源文件运行前后 SHA256 均为 `95993aab130d1ffa2ea9cb9367911f8216f90b2268b076136908f7130053b6d2`。统一／批入口各 35 条 artifact 链接存在，报告相对链接已核验。完整结果保留在服务器 `results/`，仓库只含验证摘要；原始数据不分发，许可未知。
- 源码全量 **846 passed / 23 skipped**，专项 **112 passed**（集合重叠，不相加）。wheel/sdist 构建成功；干净基础 venv 安装及 `pip check` 通过，仅本包、NumPy/PyYAML 与 pip，可发现 66 项，无格式／GPU／模型依赖导入。wheel 的 **209 个 Python 文件**与源码逐字节一致。仓库外 wheel 专项 **112 passed**，并以导出配置重跑真实 MCAP，两臂诊断数值与源哈希一致；结果见[验收记录](../validation/x1_pro_mcap_20261102/README.md)。23 项跳过沿用可选官方 LeRobot、研究桥、GPU 扩展和未分发注册证据范围，不新增这些能力的验收。
- 迁移边界：[配置](CONFIGURATION.md)、[显式时间轴](TRANSFORMS.md)。本轮未实现设备对时、其他批次物理适用性、真实任务识别、GPU／价值能力或数据发布许可验收；九项状态齐全不等于九项通过。

## 直线/升降关节位置单位（2026-10-30）

- Numeric_Check 适配器升为 **ir-adapter-21**。`joint_space.position`／`joint_q` 不再固定为 `rad`：按字段声明解析为角度（`rad`）或直线（`m`）；`joint_positions`／`cmd_positions` 的执行单位、推荐 `required_unit`、限位和相邻样本差标签都随字段。未知或 `rad`／`m` 以外的量纲继续阻塞；`command`／`feedback`、`calibration_status`、`quantity`、`semantic_status` 门槛未放宽。
- 阈值数值不变。米制字段的限位和相邻样本差按米解释，但不自动缩放，也不声称默认阈值已经过米制标定；角度字段的评分、阈值和标签仍为 `rad`。
- 本地最小验证配置（不随仓库分发）现可通过 `edc robot confirm-numeric`（exit 0），升降轴保持 `m`；它只声明 `/lift/joint_states`，不修改官方 `configs/robots/x1_pro.yaml`。CLI 只写新文件，不覆盖源配置。此前该声明会因 `joint_space.position → rad` 被拒绝。
- 指定真实 MCAP 的最小复现：`Numeric_Check` 只选 `/lift/joint_states`，记录 `completed`，1 个字段 / 1 个物理检查；`/lift/joint_states.position` 为 `physical_checks_applied=true`、`unit=m`、`source_unit=m`、形状 12069×1、`ACCEPTABLE`/100 分，`joint_step` 单位 `m per adjacent sample`，数值冻结单位 `m`。升降限位本次未配置，因此 `coverage_complete=false`，不是升降单位失败。整机 44 字段 / 17 物理检查的结果来自本地完整 profile，只作历史证据，不是本复现依赖。
- 新增 5 项测试（米制接受并标注、非位置量纲拒绝、量纲/物理量冲突拒绝、检测事件单位随字段、报告阈值表使用字段单位），更新 2 项旧的“长度关节不进入物理检查”断言；源码全量 **835 passed / 23 skipped**（`leju-kuavo` 分支；`main` 为 769）。wheel／sdist、干净 venv 安装和 `pip check` 沿用同一补丁在 `main` 上的验证结果；安装包上新增专项 **5 passed**。报告全局阈值表不再固定标 `rad/样本`，改为“字段单位/样本”，与逐字段的 rad/m 执行单位一致。原始 MCAP、既有运行结果和历史文档中的数字未改写。
- 说明与复现命令：[直线/升降关节位置单位修复记录](../validation/lift_length_unit_20261030/README.md)。

## Action_Smoothness 参考时间轴与残差 jerk（2026-10-30）

- 使用者算法端新增 `timeline.mode=native/reference_channel`、参考源 topic／逻辑名、acquisition 来源、线性重采样及 clock 策略；机器人／数据集端继续提供单位、反馈语义、clock 和明确映射，不按时间值接近推定共同 clock。默认 native，默认参数标定频率 30 Hz；参考示例见 `configs/algorithms/smoothness_reference.yaml`。
- 按用户确认恢复原残差 jerk 方法：Savitzky-Golay → `0.1 × 全局峰值速度` → 原低速段形态学 → 原位置减平滑位置 → 按逐点实际时间求三阶导 → 静止段 RMS。恢复边界膨胀口径并保留其额外纳入帧证据，不沿用上一阶段 filtered_position／均匀时间网格及边界保护的计算口径。SLAM 冻结方法保持原实现；默认评分公式与 5／15 m/s³ 基线不变，显式阈值进入同一公式。
- 参考模式只读取相机时间，不解码或编码图像。位置先经 SI 执行视图，再应用明确 clock 映射并插值到真实参考时间；仅取重叠范围，不外推。缺共同 clock／映射时不执行 jerk，返回 `NEEDS_REVIEW`、`score=null`、`assessment_complete=false`。原生 250 Hz 与默认 30 Hz 标定不匹配时，原 jerk 仅作为诊断留存，不能作该阈值的质量结论；`expected_rate_hz` 不生成时间，默认频率容差为 20%。
- 每个实例保存 `timeline.json`，包含原始与目标秒制时间、参考保留／排除行、插值左右源行和权重、clock 参数、源映射、时间范围、实测间隔及滤波窗口时长。双臂分别执行，共用的头部参考轴不进入另一臂位置选区；推荐和执行使用同一绑定与门槛。缺依赖、缺物理语义及配置冲突保持明确阻塞。
- 真实 X1 Pro 同一 MCAP／同一右臂输入复现：native 12,070 帧，残差 jerk RMS **371.792612 m/s³**；正式参考模式因两路 clock 仍未确认而待复核。独立假设对照在头部真实 acquisition 时间上得到 1,432 帧、**3.743689 m/s³**；均匀 30 Hz 诊断为 **4.089634 m/s³**。假设对照不能证明原采集同步，也不是正式质量通过。源 MCAP 和机器人配置 SHA-256 保持不变，未把假设写回配置。
- 新增时间轴专项 **23 passed**；源码及源码外 wheel 全量各 **764 passed / 23 skipped**。wheel 206 个 Python 文件与源码一致；干净基础环境只有本包、NumPy/PyYAML 与 pip，`pip check` 通过，发现仍为 66 项且未加载模型／GPU库。基础隔离时移除宿主 ROS 的 `PYTHONPATH`；完整 wheel 测试复用本机可选依赖。23 项跳过范围沿用前一基线，不新增官方 LeRobot 或 GPU 验收。
- 说明及可复现命令：[本轮验收](../validation/smoothness_timeline_20261030/README.md)。此前 filtered_position 结果作为历史证据保留，与本轮方法版本 `smoothness_stationary_residual_explicit_dt_v8` 不直接比较。

## 指标池配置与执行契约（2026-10-29 至 2026-10-30）

- 55 项指标通过 `data_requirements` 声明通道、字段、SI、语义、标定和时间条件；M8/M9 分别声明 `dataset_file/tf_chain`。声明覆盖包括暂不可执行项，不把登记、实现与当前可用混为一谈。
- `channel_param` 支持字符串数组，逐路解析逻辑名并检查最低通道数；`required_channels` 和 `field_map` 共用实际来源绑定。可选数组未指定时显示 `deferred`，指标预检解析默认范围后执行相同的声明检查并记录实际绑定；没有默认范围预检的插件明确缺输入。不同 clock 不因各自非空而视为共同 clock。纯相机时间检查声明 `frames_required=false`，无需解码图像，画面检查仍要求可读帧。
- 修复默认 `channels: null` 的绑定记录错误、自定义数组漏解析、桥接层不支持的构造参数和数值内容检查多传参数的问题。图像、位姿及同步的可选执行依赖纳入推荐条件；M3 拒绝相互冲突的选择范围，M8 不要求无关传感器。
- 合成执行核验覆盖图像、IMU、位姿、同步、文件及话题完整性；J1/J4/J5 默认与显式选择同一双臂时原始检测输出一致，A5 实际读到 `90 deg → π/2 rad` 且指令/反馈不混用，源文件哈希不变。底层计算的 110 个 Python 文件相对 `6c8d085` 全部未变；方法、评分及阈值不变。
- 指标输出继续是原方法证据：`score/passed/verdict=null`、`assessment_complete=false`，执行完成不自动成为质量放行。
- 契约专项 **71 passed**；源码及安装 wheel 全量各 **741 passed / 23 skipped**，集合重叠、不相加。安装包全量在仓库外运行，可选测试依赖复用本机环境；基础依赖另以干净 venv 验证。wheel/sdist 构建通过。23 项跳过为 16 项官方 LeRobot 依赖、3 项研究桥、3 项 GPU 扩展和 1 项未分发真实注册证据；本轮未新增真实机器人全量、官方 LeRobot 或 GPU 验收。
- 证据与例子：[指标契约验收](../validation/indicator_contracts_20261029/README.md)、[摘要](../validation/indicator_contracts_20261029/validation.json)。CI 增加安装包的 57 项输入声明覆盖检查。

## X1 Pro Reset 标定接入（2026-10-29）

- 用户确认历史 `quanta_x1` 标定文件与当前 X1 Pro 为同一台机器人。X1 Pro 配置接入 `edc_reset_calibration_v2`，使用左右末端反馈的三维 `position` Home、`tolerance1` 和 `tolerance2`；源单位和执行单位均为 `m`，作者标识沿用所有 CPU 方法的 `Repository contributors`。
- Reset v2 按每个坐标分量保留首帧／末帧误差和双阈值分数，兼容原有单一 `reference/tolerance` 配置。实际 MCAP 上已能解析 `pose/arm/left/feedback` 与 `pose/arm/right/feedback`，本次结果为 `NEEDS_REVIEW`，不是标定缺失；具体误差在运行记录中保留。
- 历史左右 `joint_pos` Home 为 7 维，当前 MCAP 反馈为 6 维，未确认关节顺序映射，配置标为 `deferred` 并不执行。姿态、夹爪、底盘、升降和头部历史项也逐项记录为延期，原因是当前字段、单位或标定对应关系尚未确认；没有因同机确认而虚构通道映射。
- Numeric_Check 方法、评分和阈值未修改；其作者仍为 `Repository contributors`，`numeric_review` 仍需配置确认人对当前阈值和物理字段完成正式确认。作者标识与其他内置 CPU 方法一致。

## Action_Smoothness jerk 输入修正（2026-10-29）

- 修正动作平滑度算法：Savitzky-Golay 滤波后，在平滑位置轨迹上计算三阶 jerk；原始位置与平滑位置的高频残差不再参与 jerk，避免测量噪声按 `1 / dt^3` 放大。
- 输出记录新增 `jerk_signal=filtered_position`、滤波参数、速度分布和时间轴重采样证据；动态帧不再因形态学填充进入静止 jerk，滤波边界增加过渡保护带；适配版本升为 `ir-adapter-18`。旧版本结果不改写，重新运行才使用新口径。
- 对同一真实 X1 Pro MCAP 的 `/right_arm/end_pose` 复算：jerk RMS 从旧口径 `365.34 m/s³`，先降为滤波位置口径约 `98.36 m/s³`，再经均匀时间网格、动态帧排除和边界保护后为 `50.89 m/s³`，最大静止 jerk 为 `454.84 m/s³`。采集时间间隔中位数为 `3.992 ms`；容器时间存在 `0.038–11.403 ms` 抖动，不能用于证明同样的 jerk 结果。该数值仍受 11 点滤波窗口和静止分段影响，不能单独解释为真实机械振动。
- 相关回归 **28 passed, 1 skipped**（`test_cpu_refinements.py`）。仓库全量测试未作为本次验收结论：当前环境缺少 `xxhash`/`lark`，且工作区已有未提交的插件和配置改动导致若干无关失败。

本文件按阶段保留历史验收。0.2.0a2 的公共姿态读取契约以
[姿态自动统一与迁移说明](ORIENTATION_HAND_TACTILE.md)为准。
本次源码提交不分发真实原始数据或详细运行材料；
文中未随仓库分发的历史证据以“本地留存”标识。

## 乐聚手部源字段读取修复（2026-10-27）

- 修复 `kuavo_msgs/robotHandPosition` 解码：实际的
  `left_hand_position` 与 `right_hand_position` 分别进入 Channel，不再从 topic
  名推断不存在的 `position`。ROS1 bag、ROS2 DB3/MCAP 与独立 ROS MCAP 共用类型优先规则；
  缺字段明确失败，保留数组类型、分量顺序和各自时间轴。
- 实际乐聚 bag 中两侧各为 **666×6、uint8**，与 rosbags 直接解码逐元素一致；原始
  header/container 时间、行映射和 bag SHA-256 `2b49e6fe9505eeb32635cfc21d1440ed4464a1cddcd1ae6001745b1668621126`
  未变。不存在的 `/control_robot_hand_position.position` 读取被拒绝。
- 同一默认配置下 `Numeric_Check` 从此前的 `processing_error`（缺 `position`）变为
  `completed`，共 **11 个字段报告**，手部两字段均完成有限值检查；单位、物理量、指令／反馈
  仍未确认，`physical_checks_applied=false`、`assessment_complete=false`，不宣称物理质量通过。
  `/kuavo_arm_traj` 的空 velocity/effort 仍明确未评估。
- 未修改算法目录；此前清单中的 **77 个算法相关 Python 文件**哈希一致，方法、评分和阈值不变。
- 新增 10 个回归用例；仅含本次修复的提交快照源码 **698 passed / 7 skipped**，
  仓库外当前 wheel 的手部专项 **10 passed**。此前本地完整工作区的 712 项通过结果
  另含 14 项尚未随本次提交的工作流测试，不能混作本次提交的用例数量。
  wheel 在仓库外对同一真实 bag 读出两字段并完成 Numeric_Check；干净基础安装仅
  NumPy/PyYAML，9 项内置算法可发现，未强制安装 ROS、GPU 或模型依赖，`pip check` 通过。
- 证据：[修复说明](../validation/robot_hand_position_20261027/README.md)、
  [安装摘要](../validation/robot_hand_position_20261027/publication-package.json)。真实 summary 本机留存：
  `/mnt/public/dht/dqs_development_results/robot_hand_position_fix_20261027/summary.json`。
  这是读取适配修复，不代表该机器人其他自定义消息或物理条件已完成验收。

## 可选质量方法与变换复检（本地工作区，2026-10-27）

- 默认工作流只运行 quality；价值评估由使用者显式配置。CPU 与 GPU projection alignment 的接入位置均为 quality，设备、模型和输入条件决定可用性。真实 alignment 尚未迁入公共包，本轮没有运行真实 GPU 推理或真实价值模型。
- 默认选择可运行方法并保留不可用原因；显式要求的方法未完成、运行失败或部分输入未评估不能视为通过。报告流程按实际配置展示，不再固定显示必经 value。
- 编辑和增强的每个输出分支必须先接质量复检；缺失复检、复检父版本或绕过复检继续变换在创建输出前拒绝。新版本不能沿用旧版质量放行，复检失败不发布。旧配置需显式补充复检阶段，迁移边界见 [工作流](WORKFLOWS.md)。
- 新增 14 项契约测试，覆盖可选 GPU 缺失／可用／关闭／显式要求／执行失败、可选价值、变换分支、复检失败及价值后编辑。GPU 情况使用契约替身，仅证明编排行为。
- 源码全量 **686 passed / 23 skipped**；仓库外安装 wheel 全量 **686 passed / 23 skipped**。wheel／sdist 从隔离暂存的受版本管理文件构建；干净基础安装只有框架、NumPy、PyYAML 与 pip，核心算法池为 66 项（默认 CPU 9 项），未导入 Torch，pip check 通过。跳过项未算作通过。
- 证据：[摘要](../validation/workflow-selection-20261027/summary.json)、[源码回归](../validation/workflow-selection-20261027/source-tests.log)、[安装包回归](../validation/workflow-selection-20261027/wheel-tests.log)。本轮变更尚未提交或推送 GitHub；下方历史交付记录保持原状。

## 默认姿态统一与 GitHub 交付范围（0.2.0a2，2026-10-27）

- 公共数据入口、配置入口、CLI 和算法输入的 orientation 自动统一为 N×4 XYZW
  单位四元数；明确声明的 RPY（固定 XYZ、deg/rad）、矩阵和 WXYZ 在读取时转换。
  原时间、clock、坐标系、源行与 selector 保留，不因此确认采集同步或标定。
- 未知表示明确阻塞；非法旋转不静默修复或丢行。原始审计通过
  inspect_recorded_signal / recorded_channels 或 open_source_dataset 访问。
  Numeric_Check 的原姿态有限性诊断保留 NaN/Inf 原分量，不伪称物理姿态验收。
- 每次执行保存 orientation-normalization.json，区分转换计划、实际读取和失败。
  episode 与 dataset 插件的读取证据均验证，算法之间重置计数。
  unit_policy: manual 不关闭姿态统一；迁移边界见 [说明](ORIENTATION_HAND_TACTILE.md)。
- 源码全量 **672 passed / 23 skipped**；从隔离暂存目录构建后，源码外安装 wheel
  全量 **672 passed / 23 skipped**；官方 LeRobot 环境专项 **135 passed / 3 skipped**。
  集合重叠，不相加。全量跳过为 16 项官方可选依赖、3 项研究桥、3 项 GPU 扩展
  和 1 项未分发真实注册证据；官方依赖另在专项环境验证。
- wheel / sdist 构建通过；全新基础环境仅 pip、NumPy、PyYAML 和本包，66 项核心算法
  可发现（默认 CPU 9 项、内部指标 57 项），RPY 默认读取实际得到 XYZW，pip check 通过且未安装或导入 Torch。
- 真实 MCAP 的独立 odometry 配置读出 **2,414×4** XYZW；源文件 SHA-256、
  原数组、原元数据及时间戳保持不变，与原姿态的旋转矩阵最大差为 **1.31e-18 以下**。
  不将此结果扩展为真实 IMU、灵巧手或触觉的物理验收。
- 本次提交包括上述功能及此前已验收的 IMU、odom、通道词表、手/夹爪/触觉模板和
  数值补项；原始数据和详细真实运行材料留在本地。
  GitHub 仓库保持私有；代码上传不代表公开分发许可已完成。

可分发验证摘要及复现命令见 [本轮验收](../validation/canonical_orientation_20261027/README.md)。
安装包验证完成后只追加说明与验收摘要，运行时代码未再改动。


## EDC 统一配置与复检路径验收（2026-10-26）

- 公共 Python／CLI 为 `edc`，安装包 `embodied-data-curation`；实现位于 `src/edc/{data,robots,algorithms,execution,workflows,reporting}`。固定结果协议仍为 `dqs_record_v1`（DQS：Data Quality Service）。
- 总入口 `edc_config_v1` 引用 dataset、robot、algorithms、workflow，可导出完整单文件并保存运行快照。单臂 main、双臂 all／单侧选择、缺侧不降级、同机器人两任务复用均有实际执行测试。
- X1 Pro 32 路 topic／23 个逻辑名已合并；六路末端 XYZ 的米制语义已登记，未知关节单位、深度比例、标定和共同 clock 保持未知。
- 默认执行完整与部分可运行项；推荐和执行共用实例绑定。部分覆盖不会得到完整质量放行；SDK 示例不进入自动生产池。
- 编辑只处理明确失败样本，保留原先达标样本；副本经过一致性与来源校验后重新质量检查。缺输入／待复核样本单独挂起，不会在交接时消失。
- 价值方法各自筛选轨迹，显式子集后重新检查集合条件；分别保存输入／输出引用，下阶段只接收成功完成的集合。增强新版本经同样校验及质量复检；不达标、缺输入或待复核时 `delivery_status=suspended`。
- 复核后恢复执行重新计算 gate；数据、配置、代码、参数、依赖和证据匹配才复用。返回通过有限显式阶段实现，不自动无限编辑。

| 验证项 | 当前实测 | 依据 |
|---|---|---|
| 源码回归 | **484 passed / 21 skipped**，Python 3.13 | [source-tests.log](../validation/edc_20261026/source-tests.log) |
| 仓库外 wheel 回归 | **484 passed / 21 skipped**，独立 Python 3.12 venv | [wheel-tests.log](../validation/edc_20261026/wheel-tests.log) |
| 官方 LeRobot 环境专项 | **67 passed / 2 skipped**；包含重叠测试，不与全量相加 | [official-tests.log](../validation/edc_20261026/official-tests.log) |
| 源方法核对 | 213 个源快照文件未变；50 个 Python 方法文件在仅归一化模块路径后 AST 一致 | [method-migration.json](../validation/edc_20261026/method-migration.json) |
| CPU 行为 | 同一合成 MCAP、同参数，9 项状态与原结果一致 | [cpu-comparison.json](../validation/edc_20261026/cpu-comparison.json) |
| 完整示例与恢复 | 2→2→2→2→2→4 条输入；六阶段完成并在恢复时全部复用 | [workflow.json](../validation/edc_20261026/workflow.json) |
| 基础安装 | 仅 NumPy／PyYAML，9 内置算法，无 Torch；pip check 与两条结构示例通过 | [acceptance.json](../validation/edc_20261026/acceptance.json) |
| 真实 X1 Pro MCAP | 6 方法／7 记录（平滑度分双臂），处理完成、质量挂起，原始哈希不变 | [x1-pro-mcap.json](../validation/edc_20261026/x1-pro-mcap.json) |

真实 MCAP 是本轮中间版本的输入与双臂绑定验证（记录 ir-adapter-13），不是最终全部功能或整机质量通过。最终子集／复核路径另由上述安装包测试验收。真实价值、编辑、增强效果与许可仍逐项待验收；接口示例不证明收益。CI 已调整到 EDC 配置与完整示例，远端实测状态以 Actions 为准。

## 仓库内质量指标池

质量指标已经并入 `src/edc/algorithms/quality/indicator_core`，由本仓库直接登记和执行，不依赖单独的指标发行包。
原有 9 项 CPU 方法保持 `quality_cpu` 默认预设；相机、IMU、激光、关节、命令、位姿和跨模态指标使用 `quality_indicators` 预设单独选择。
清单中的 `implemented: false` 只表示当前版本缺少该指标的实现或输入适配，推荐池会明确返回缺输入／未实现原因，不会伪装为通过。

## 初次迁库基线（0.1.0a3）

| 项目 | 实测结果 | 依据 |
|---|---|---|
| 迁移基线 | 原工作区选定 213 文件，迁移前后源文件无变化；9 项方法目录 60 文件逐字节一致 | [migration.json](../validation/migration.json)、[基线](../migration/source-manifest.json) |
| CPU 行为对照 | 同一合成 ROS2 MCAP、同参数；9 项均 completed，原结果逐项一致 | [cpu-comparison.json](../validation/cpu-comparison.json)、[复现脚本](../../scripts/compare_cpu_methods.py) |
| 源码完整回归 | **465 passed / 21 skipped** | [测试摘要](../validation/tests.json) |
| 源码外 wheel 完整回归 | **465 passed / 21 skipped**，从 /tmp 运行，导入安装包 | [测试摘要](../validation/tests.json) |
| 最新工作流契约测试 | **37 passed**；最后一次代码变更后重验 | tests/edc/test_workflows.py、test_workflow_inputs.py |
| 官方 LeRobot 专项 | 独立官方依赖环境 **20 passed**；这是单独运行，不与完整测试简单相加 | tests/edc/test_lerobot_official.py、[摘要](../validation/tests.json) |
| 干净基础安装 | wheel 在无系统包、清除继承 PYTHONPATH 的 venv 中导入；仅 pip、NumPy、PyYAML、embodied-data-curation；9 算法、未导入 Torch、pip check 通过 | [base-install.json](../validation/base-install.json) |
| 源码外完整示例 | 2→2→2→2→2→4 条阶段输入；全部阶段完成，恢复时六阶段均复用 | [wheel-workflow.json](../validation/wheel-workflow.json) |
| 打包 | wheel 与 sdist 构建通过；wheel 包含 143 Python 文件、9 CPU 方法及复位配置，不含 GPU 方法／权重／私有 wheel | [package.json](../validation/package.json) |
| GitHub 远端交付 | `main` 首次推送完成，本地与远端提交 `412a877` 一致，包含核心迁移及朋友验收指南 | [embodied-data-curation](https://github.com/singlesingerdht/embodied-data-curation) |

CPU 对照仅排除 `process_time_ms`、`adapter_elapsed_s` 和输出工件根路径，不排除评分、阈值、事件或判断。对照数据为新生成的合成数据，不冒充真实机器人重新验收。

21 项跳过包括：14 项需要本环境未安装的官方 LeRobot，3 项未迁研究价值桥，3 项 GPU 扩展，1 项未分发的真实注册证据。官方专项环境单独验证读取与转换；本轮没有把跳过算作通过。

## GitHub CI 兼容修复

首次远端矩阵在 Python 3.10／PyArrow 25.0.1 下有 23 项失败：Parquet 数值 dtype 转换隐式调用了未安装的 pandas。Python 3.12 的单测通过，但后续步骤被矩阵的联动取消机制停止。原失败日志见 [Actions 40278894553](https://github.com/singlesingerdht/embodied-data-curation/actions/runs/40278894553)。

读取层现直接按 Arrow 数值类型和位宽恢复 NumPy dtype，避免 pandas 依赖；保留大整数、浮点精度、NaN／Inf、布尔值、列表形状以及原 null／不齐整列表行为。新增在隔离进程中禁用 pandas 的回归用例。基础依赖仍只有 NumPy／PyYAML，算法方法、评分公式和阈值不变。矩阵改为 `fail-fast: false`，不同 Python 任务各自完成。

本地 Python 3.12＋PyArrow 25.0.1 的源码及源码外 wheel 全量检查均为 **466 passed / 21 skipped**；干净基础 wheel 安装、完整合成工作流和恢复执行也通过。该组结果与上方迁移基线分别记录，见 [ci-compatibility.json](../validation/ci-compatibility.json)。远端修复复验状态见 [Actions](https://github.com/singlesingerdht/embodied-data-curation/actions)。

## 已实现的边界

- 公共薄入口、Dataset→Episode→Channel、逻辑通道目录、配置与 SI 执行视图。
- 原生格式读取与可选官方 LeRobot；能打开文件与整机支持分别验收。
- 推荐、预检、外部插件执行共享逻辑名绑定。未知物理量／确认状态在两种后端都阻塞物理读取；原值有限性检查保留。
- Check_Timestamp、Sync_Check、Root_Cause 实际消费显式 target_clock 映射；原时间／时钟／来源、频率及 identity 行映射保留。不重采样，不仅靠目标名称证明共时钟。
- 有限工作流、精确数据引用、只读上游、dataset 集合调度、版本化放行和显式子集。
- 100 分与待复核可并存；追加复核不覆盖评分、不补未执行项；同一版本多阶段质量约束累积。
- 编辑／增强副本回读、逐项一致性与来源验证；失败输出隔离；增强仅在价值之后。v1 限定 identity 行映射。
- 恢复校验数据、配置、代码、算法规格／依赖版本及证据；改变参数／版本、删除证据均会失效。
- 中英 README、贡献指南、协作任务、CI、来源和发布清单。单次入口与旧结果保留兼容，见 [BATCH_MIGRATION](BATCH_MIGRATION.md)。

## 尚未完成

- 真实价值／编辑／增强方法未通过本公共流程逐项验收；盘点及亮度变换只是合成接口示例。
- 通用非 identity 变换、segment 调度、分布式执行、平台账号／权限／前端和真实 VR 加速未实现。
- 初次迁移基线使用 Python 3.13；CI 兼容修复另有本地 Python 3.12 验证。Python 3.10／3.12 远端矩阵的实测状态以 Actions 为准，不能把首次失败或取消算作通过。
- 仓库级许可、算法／第三方／模型与真实数据分发范围仍待核查；不宣称已公开发布。

## 复现

```bash
pip install -e '.[formats,test]'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/edc -q -rs
python -m build
python scripts/verify_migration.py --source /path/to/original-repository --output /tmp/migration.json
python scripts/compare_cpu_methods.py --source /path/to/original-repository --output /tmp/new-comparison
```

对照输出目录必须为全新目录。独立 wheel 的完整流程见 [README](../../README.zh-CN.md) 与 [WORKFLOWS](WORKFLOWS.md)。原数据、GPU／研究环境及历史真实报告仍在原仓库，本轮未修改。


## 子项完成度与 IMU 联合输入优化（2026-10-26）

- 修复数值子项未完成但质量放行可能为 keep 的问题。直接运行与工作流共用完成度判断，检查嵌套 check_coverage、缺失结果和声明范围；新策略为 episode_filter_v2 / workflow_gate_v2。缺参数、缺输入、样本不足保持 unassessed，明确失败仍保留 exclude。
- Numeric_Check 的历史分数、verdict 和 assessment_complete 原值保留；新增 coverage_complete 反映真实子项覆盖。旧 Numeric_Check 记录缺少分项证据时不追认为完整；历史文件不改写，恢复执行受已有代码/规格指纹约束而重新核验。推荐列表分别展示物理前置条件与子项阻塞原因，报告汇总也检查分项覆盖。
- Numeric_Check 升为 ir-adapter-15；同一 IMU 通道内的 ang_vel / lin_acc 在单位、语义、校准、确认状态、N×3 形状与样本数满足时联合调用既有偏置判据。保留原逐字段评分，新增证据与源行映射；加速度偏置线索进入复核。沿用既有陀螺仪区段选择器，不声称独立证明静止；无有效区段不算通过。跨通道自动配对、猜测时钟和重采样均未引入。
- 保持图像 / 视频双入口：LeRobot dtype=image 走 frames，dtype=video 走 video。现有 HDF5 RGB 转换路径将图片保存为嵌入 PNG，不生成 MP4。新增官方环境断言：源时间 [9.12345678901234, 10, 11] ms 与索引播放时间 [0, 1/30, 2/30] s 分别保留，逐行映射一致；没有用索引时间替代源采集时间。本轮未修改读取器、转换器或时间映射。
- Dedup_Check 已有全量重复时间戳、相邻重复和非相邻复用统计，本轮不重复开发；它不证明图像或数值内容复制。原编码突变检查不纳入本轮。
- 复核后源码回归 **499 passed / 21 skipped**（Python 3.13）；仓库外全新 wheel 环境回归 **499 passed / 21 skipped**（Python 3.12）；安装 wheel 的官方 LeRobot 环境专项 **76 passed**。三组包含重叠用例，分别报告。21 项跳过为 14 项官方依赖、3 项研究价值桥、3 项 GPU 扩展和 1 项未分发真实注册证据；官方专项另验读取与转换。
- 基础 wheel 仅安装 NumPy / PyYAML，9 项内置算法发现、结构检查和 pip check 通过；仓库外完整合成工作流六阶段完成，编辑及增强均经过质量复检，恢复时六阶段全部复用。60 个内置方法文件与基线逐字节一致。源码分发排除示例读取器的构建目录；文档和报告同步分项完成度策略。
- 详见 [validation.json](evidence/github_review_20261026/validation.json)、[源码回归](evidence/github_review_20261026/source-tests.log)、[官方专项](evidence/github_review_20261026/official-tests.log)、[安装包回归](evidence/github_review_20261026/wheel-tests.log)及[工作流验证](evidence/github_review_20261026/workflow.json)。均为合成回归，不冒充真实机器人新增验收；真实机器人限位、阈值与校准信息仍由实际规格确认，本轮未猜填配置。

## 数值检查补项：独立指令限位与冻结候选（2026-10-26）

- 在现有 Numeric_Check 中补 A5 角位置指令限位，以及 I7 / J7 / A7 的 IMU、关节反馈、动作指令数值冻结候选。沿用原方法并将新增指标登记在仓库指标池；具体参数、合成例子和未覆盖范围见 [补齐说明](NUMERIC_GAPS_20261026.md)。
- 指令限位独立配置为 command_limits.limits，逐分量闭区间检查，源值通过既有 SI 视图换算；不借用反馈限位。新越界结论进入筛选 exclude，原 score / passed / verdict 及检测器子项保留。未配置、维度不符、短输入、未知单位或陈旧确认保持未完成，不冒充 keep。
- 数值冻结保留 Episode 内逐分量区间、原文件、selector、源分量和时间证据。普通恒值仅观察，运动后恒值进入复核，不能据此确认采集故障。末尾区间不遗漏；全幅差阈值避免累计漂移被判整段冻结，近恒值窗口可能重叠且不作为独立样本相加。正常动作保持可经复核排除误报。
- 时间跨度只使用当前通道有限、严格递增且逐行对应的 acquisition / container 记录时间；未知、播放、合成、重复或回退时间只报告源行区间。LeRobot 图像 / 视频读取入口、转换与源时间映射未修改。新结果与完整事件 CSV 由现有 catalog / _runs 发现，HTML 展示源分量、行区间、时间跨度与分项状态。
- Numeric 适配器升级 ir-adapter-16；筛选策略为 episode_filter_v3 / workflow_gate_v3。新增阈值纳入确认摘要，旧确认需审阅新增项后重新签署；未代替机主确认真实配置。旧直接 assess 入口仍仅执行原检测器；补项通过注册算法 run(context, params) 执行。历史输出不改写。
- 新增 **43 个合成回归用例**。源码全量 **542 passed / 21 skipped**；仓库外安装 wheel 的全量 **542 passed / 21 skipped**（新安装目标目录，复用已有可选依赖环境）；全新基础 venv 安装 wheel 专项 **92 passed / 2 skipped**。官方 LeRobot 源码专项 **93 passed**。这些集合重叠，分别记录，不能相加。全量 21 项跳过仍为 14 项官方依赖、3 项研究桥、3 项 GPU 扩展、1 项真实注册证据；基础专项另跳过 scipy。
- wheel / sdist 构建通过，基础运行依赖仅 NumPy / PyYAML；9 项默认 CPU 方法和仓库指标池可发现，pip check 通过。基础测试额外安装 pytest 及其依赖。没有修改原始数据或真实机器人配置。验证与源码摘要见 validation.json（本地留存：evidence/numeric_gaps_20261026/validation.json），包信息见 package.json（本地留存：evidence/numeric_gaps_20261026/package.json）。本轮仅覆盖上述两类缺口，指标池中仍有部分项目待实现或待输入确认。

## 新增数值项的真实 topic 与输出核验（2026-10-27）

- 使用现有 X1 Pro 自包含配置，在一个明确指定的真实 MCAP 上重跑源码 Numeric_Check ir-adapter-16；未修改真实配置。左右反馈与左右主臂指令的四个 joint_states topic 均绑定到正确逻辑名和 position 字段，完整保留各自源行与 header.stamp。
- 实际选中 17 个数值通道、产出 44 个字段报告，均完成有限值检查；catalog、records、result.json、HTML、selection 已写入独立运行目录，errors.jsonl 为空。原始 MCAP SHA-256 与此前基准一致，配置与运行快照一致。
- **物理项尚未完成真实验收**：44 个字段的 physical_checks_applied / coverage_complete 均为 false，最终 selection 为 unassessed。反馈和指令冻结等待单位、语义、校准和当前阈值确认；独立 command_limits.limits 未配置。不能将执行 completed 理解为物理检查通过。
- 源 MCAP 的 32 个 topic 中没有声明 sensor_msgs/msg/Imu，profile 也未声明 IMU，本次未验证 I7 的真实输入。共同 clock 未确认，不据此宣称跨通道采集同步。
- 相关专项回归 **77 passed / 3 skipped**；本轮未变更实现或重新构建安装包。核验表、具体阻塞项与实跑证据见 topic_binding_20261027（本地留存：evidence/topic_binding_20261027/README.md） 及 validation.json（本地留存：evidence/topic_binding_20261027/validation.json）。

## IMU 中间层声明与数据配置入口（2026-10-27）

- 在既有 IMU 读取和 Numeric_Check 上增加标准逻辑名 imu/main、imu/base、imu/head、imu/chest、imu/wrist/{left,right,main}，支持实例后缀；不推断源单位、安装位置或校准，不复制检测器。
- 新增 configs/imu.yaml 入口及 robot / dataset / algorithm 子配置。角速度、加速度、姿态分别声明字段与维度；单位、clock、校准保持未知。dataset.source_bindings 的 null 明确表示待绑定并拒绝运行，缺失源 topic 不回退其他信号。
- 独立 IMU 配置不再被自动注入机械臂范围；机械臂拓扑未知仍阻塞，显式 arms 继续校验。新模板为 opt-in，没有向现有 X1 参考样例虚构 IMU 通道。
- 新增 **18 个测试用例**，覆盖逻辑名冲突、待绑定、批次单位、源时间、缺失 topic、四配置工作流，以及合成 ROS2 Imu 经中间层到数值检查和 catalog / HTML / CSV 的链路。合成确认仅适用于测试生成的数据，不替真实设备签署。
- 源码全量 **560 passed / 21 skipped**；源码外安装 wheel 全量 **560 passed / 21 skipped**；相关配置专项 **69 passed / 3 skipped**。这些集合重叠，不能相加。21 项跳过仍为 14 项官方 LeRobot 依赖、3 项研究桥、3 项 GPU 扩展和 1 项未分发真实注册证据。本次未重新进行官方 LeRobot 环境专项。
- wheel / sdist 构建及干净基础安装通过；基础仅 NumPy / PyYAML，9 项内置算法可发现，未导入 Torch，pip check 通过。SDist 包含新配置与测试。
- 实查原 X1 MCAP 的 32 个 topic 和附带元数据，未发现 Imu 消息或 IMU 字段；/odom 仅提供里程计 pose / twist，没有线加速度及 IMU 来源声明。真实 IMU 接入仍待提供有效 source_bindings 与字段确认。见 [配置说明](IMU_CONFIGURATION.md)、源 topic 清单（本地留存：evidence/imu_configuration_20261027/source-topics.json） 和 验收证据（本地留存：evidence/imu_configuration_20261027/validation.json）。


## 通道目录参数化与覆盖盘点（2026-10-27）

- 标准逻辑通道从 38 项扩展为 107 项，覆盖现有 21 类模态；旧 38 项名称及约束保留。单一来源为随 wheel 分发的 src/edc/data/channel_registry.yaml。新公共入口 edc.load_channel_registry，CLI edc channels 列词表或检查数据集实际绑定。
- 机器人支持 channel_registry.ref 引用 YAML/JSON 团队词表，路径相对所在机器人文件；团队名使用 team__，标准名不得改写语义。指令／反馈约束校验全部已声明字段，保持单位、坐标、clock 和校准未知值。新 catalog 展示未绑定源通道和缺失声明；缺失输入不补虚构通道。
- 四配置导出冻结完整目录，run.json 保存生效目录；版本、重复键、命名冲突、循环引用明确失败。未配置词表的直接 load_profile 保持旧结构。迁移边界：重复 YAML/JSON 键以及 YAML merge 隐式覆盖不再接受，旧文件须先整理；标准目录 base_version 不匹配需审阅迁移。
- 实际源清单复用此前核查的单个 X1 MCAP：32 topic／23 逻辑绑定；剩余四路 WrenchStamped、五路 String 仍为独立解码／业务语义缺口。/odom 的完整 twist 未补；该样例没有 IMU。现真实 profile、原始 MCAP 与历史报告未修改，目录增加不等于读取器或专用算法完成。
- 新增 26 个测试；源码全量 **586 passed / 21 skipped**，源码外安装 wheel 全量 **586 passed / 21 skipped**，安装 wheel 的官方 LeRobot 相关专项 **93 passed / 2 skipped**。测试集合重叠，不相加；全量跳过仍为 14 官方依赖、3 研究桥、3 GPU、1 真实注册证据；官方专项跳过 GPU 与真实注册证据各一项。
- wheel／sdist 构建、词表打包、源码外导入、干净基础安装和 pip check 通过；基础仅 NumPy／PyYAML，9 项算法发现，未安装或导入 Torch。声明、配置和执行均用合成回归核验，不构成新增真实设备验收。
- 全部名称及真实 source topic 对照见 [CHANNEL_INVENTORY](CHANNEL_INVENTORY.md)，维护参数与兼容边界见 [CHANNEL_REGISTRY](CHANNEL_REGISTRY.md)，验证见 channel_registry_20261027（本地留存：evidence/channel_registry_20261027/README.md）。


## Odometry 完整字段与按构型配置通道（2026-10-27）

- Odometry 纳入同一 Channel 中间层，独立保持 odometry 模态。ROS1、ROS2 SQLite 和独立 MCAP 读取 position、orientation、lin_vel、ang_vel、pose_covariance、twist_covariance；NaN/Inf 原样保留，不生成 lin_acc。源 schema 优先于误导性 topic 名；将 Odometry 强制声明为 IMU/pose 会明确失败，派生信号须显式变换。
- 新增 frame_evidence，分段保存 header.frame_id / child_frame_id 及 pose/twist 字段对应关系；不自动改写坐标、不确认校准或混合协方差单位。原始 header/container 时间保持逐行对应，缺共同 clock 不宣称同步。
- 新增 odometry 四配置入口、单臂/双臂移动/四足机器人模板与四足团队词表；标准右腕 imu/wrist/right 与左腕并列展示。parts/robot_part 维护构型，逻辑名称与源绑定分开。明确无机械臂构型不注入 arms；显式选底盘不会要求无关机械臂，缺已选腕部仍为未完成。非法 arm 引用、左右冲突明确失败。
- catalog 增加部件定义和坐标来源证据。修正展开执行范围后逻辑参数来源丢失/重复问题，保留 requested → 实际 channel 绑定。新增 24 个有意义的回归用例，单臂/双臂/四足自定义名均进入算法输出。
- 指定真实 X1 MCAP /odom 的 **2,414 行、六类字段**读取与 Numeric_Check / Dedup_Check 执行完成，生成 catalog / records / HTML，errors 为空；源 SHA-256 前后一致。六字段有限性通过，但 odom 漂移、运动学一致性和协方差有效性未实现，physical_checks_applied=false、selection=unassessed。没有用 odom 冒充 IMU；本文件仍无 IMU。旧 X1 profile 的 fields=[] 未改，实跑使用独立 opt-in 配置。
- 源码全量 **610 passed / 21 skipped**；源码外安装 wheel 全量 **610 passed / 21 skipped**；安装 wheel 的官方 LeRobot/逻辑名/媒体专项 **71 passed / 3 skipped**。集合重叠不相加，官方本次选集与上一轮不同。wheel/sdist、干净基础安装、源码外导入和 pip check 通过；基础仅 NumPy/PyYAML，9 算法，无 Torch。
- 用法及迁移边界见 [ROBOT_TOPOLOGY_CHANNELS](ROBOT_TOPOLOGY_CHANNELS.md)，验收与可直接查看的真实报告见 odometry_topology_20261027（本地留存：evidence/odometry_topology_20261027/README.md）。本次未覆盖独立 Twist/Wrench/String 解码及其他新模态专用算法，不构成新增真实 IMU 或全部机器人构型物理验收。

## 中间层 topic 实际表示预览（2026-10-27）

- 从同一真实 MCAP 导出当前 X1 的完整公共 catalog 与 32 路 topic 映射表，确认 23 个逻辑绑定；保存 Channel 原始结构、时间与坐标证据及来源摘要，源文件 SHA-256 前后一致。
- 预览区分当前 X1 `/odom` 的空字段声明与独立 opt-in 入口的六字段实读，以及左右腕 IMU 的未绑定模板；不把同一源的两个配置视图计为两个真实 topic。
- 浏览器交互、8 个预览条目和 736/390/320 像素视口布局检查通过，JavaScript 异常为 0；已查看默认状态截图。本轮仅新增表示层证据与说明，未修改核心实现，未重跑全量测试或安装包构建。
- 原始 topic、逻辑名称、构型部件和真实/声明状态分别展示。真实 IMU、物理项与公开分发许可仍按既有边界处理；详见 channel_preview_20261027（本地留存：evidence/channel_preview_20261027/README.md）。

## 姿态表示转换与手部/夹爪/触觉接入（2026-10-27）

- 新增公共 `edc.convert_orientation` 和 `Episode.transform_orientation`，支持 XYZW/WXYZ 四元数、固定 XYZ/extrinsic 的 deg/rad RPY、3×3 及显式行/列优先展开矩阵互转。表示、顺序、单位必须声明；非法旋转明确拒绝，原始读取仍保留异常。仅依赖 NumPy，SciPy 用于独立对照测试。
- 转换返回 output/params/source_mapping，保留源文件、selector、实际/请求逻辑名、identity 行对应和时间/clock/坐标证据；不改源、不重采样、不做坐标系变换、不确认同步或标定。SI 视图避免角度重复换算；奇异 RPY 行与确定性四元数符号策略写入结果。
- orientation 不再默认总是四元数：通用 IMU 模板改为未知维度/表示，ROS schema 可补 XYZW 编码证据。字段级表示优先，形状与单位冲突明确失败；已有未知表示与旧读取行为保持兼容，新转换要求显式表示。
- 既有标准词表已含手部、夹爪、触觉和接触名称，仍为 107 名称/21 模态。新增手部/夹爪/触觉四配置入口，12 个可选声明通过 parts、robot_part、实例后缀和 source_bindings 维护；未绑定值为 null 并阻塞运行，未猜测 DOF、单位、标定和触觉尺寸。合成完整绑定经 Numeric_Check 输出 catalog/records/HTML，不等于新增触觉专用物理算法。
- 新增 **52 个回归用例**；源码全量 **660 passed / 23 skipped**，源码外安装 wheel 全量 **660 passed / 23 skipped**，安装 wheel 的官方 LeRobot/姿态/逻辑名/媒体专项 **123 passed / 3 skipped**。集合重叠不相加。官方环境新增 RPY/矩阵和手部/触觉保真用例通过。wheel/sdist、干净基础安装和 pip check 通过；基础仅 NumPy/PyYAML，9 算法，无 Torch/SciPy。
- 同一真实 X1 MCAP 的左右夹爪状态/指令四路实读成功；2,414 条 odom 四元数转为 RPY，矩阵往返最大误差约 8.67e-19，源文件摘要、原数组、元数据和时间证据保持不变。原 X1 odom 配置与历史报告未改。真实 IMU、灵巧手、触觉和相关物理项仍未验收；四路 Wrench、五路 String 解码/绑定缺口未由本轮补齐。
- 完整词表/32 路源清单与 24 个通道视图已纳入新预览，明确区分 10 个真实数据视图（同一 odom 两种配置）和 14 个待绑定声明；浏览器交互、状态恢复、736/390/320 像素布局及浅/深色截图核查通过，JavaScript 异常为 0。详见 [说明](ORIENTATION_HAND_TACTILE.md) 及 验收证据（本地留存：evidence/orientation_hand_tactile_20261027/README.md）。测试/打包之后仅追加文档和预览；本轮未提交或推送 GitHub，不宣称完成公开分发许可核查。
