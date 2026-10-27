# 当前验收状态

日期：**2026-10-27**。框架版本 **0.2.0a2**；Numeric_Check 适配 **ir-adapter-16**，其余 CPU 适配 **ir-adapter-14**。

本文件按阶段保留历史验收。0.2.0a2 的公共姿态读取契约以
[姿态自动统一与迁移说明](ORIENTATION_HAND_TACTILE.md)为准。
本次源码提交不分发真实原始数据、详细运行材料或待核查的 Dayan 插件；
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
- wheel / sdist 构建通过；全新基础环境仅 pip、NumPy、PyYAML 和本包，9 项内置算法
  可发现，RPY 默认读取实际得到 XYZW，pip check 通过且未安装或导入 Torch。
- 真实 MCAP 的独立 odometry 配置读出 **2,414×4** XYZW；源文件 SHA-256、
  原数组、原元数据及时间戳保持不变，与原姿态的旋转矩阵最大差为 **1.31e-18 以下**。
  不将此结果扩展为真实 IMU、灵巧手或触觉的物理验收。
- 本次提交包括上述功能及此前已验收的 IMU、odom、通道词表、手/夹爪/触觉模板和
  数值补项；原始数据、详细真实运行材料及许可未确认的独立 Dayan 插件留在本地。
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

## 独立 Dayan 插件的分发边界

此前在本地审查的 plugins/dayan 是单独接入预览，上游许可证尚未确认。
该插件、上游源码和对应真实运行材料不包含在本次 GitHub 提交或安装包中。
下面的数值补项为框架自有实现，不代表已上传全部 Dayan 方法。

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
pip install -e examples/plugins/algorithm
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

## Dayan 对照数值缺口：独立指令限位与冻结候选（2026-10-26）

- 在现有 Numeric_Check 中补 A5 角位置指令限位，以及 I7 / J7 / A7 的 IMU、关节反馈、动作指令数值冻结候选。沿用 9 个内置算法，不安装重复 Dayan 方法；新检测为仓库自有实现。具体参数、合成例子和未覆盖范围见 [补齐说明](NUMERIC_GAPS_20261026.md)。
- 指令限位独立配置为 command_limits.limits，逐分量闭区间检查，源值通过既有 SI 视图换算；不借用反馈限位。新越界结论进入筛选 exclude，原 score / passed / verdict 及检测器子项保留。未配置、维度不符、短输入、未知单位或陈旧确认保持未完成，不冒充 keep。
- 数值冻结保留 Episode 内逐分量区间、原文件、selector、源分量和时间证据。普通恒值仅观察，运动后恒值进入复核，不能据此确认采集故障。末尾区间不遗漏；全幅差阈值避免累计漂移被判整段冻结，近恒值窗口可能重叠且不作为独立样本相加。正常动作保持可经复核排除误报。
- 时间跨度只使用当前通道有限、严格递增且逐行对应的 acquisition / container 记录时间；未知、播放、合成、重复或回退时间只报告源行区间。LeRobot 图像 / 视频读取入口、转换与源时间映射未修改。新结果与完整事件 CSV 由现有 catalog / _runs 发现，HTML 展示源分量、行区间、时间跨度与分项状态。
- Numeric 适配器升级 ir-adapter-16；筛选策略为 episode_filter_v3 / workflow_gate_v3。新增阈值纳入确认摘要，旧确认需审阅新增项后重新签署；未代替机主确认真实配置。旧直接 assess 入口仍仅执行原检测器；补项通过注册算法 run(context, params) 执行。历史输出不改写。
- 新增 **43 个合成回归用例**。源码全量 **542 passed / 21 skipped**；仓库外安装 wheel 的全量 **542 passed / 21 skipped**（新安装目标目录，复用已有可选依赖环境）；全新基础 venv 安装 wheel 专项 **92 passed / 2 skipped**。官方 LeRobot 源码专项 **93 passed**。这些集合重叠，分别记录，不能相加。全量 21 项跳过仍为 14 项官方依赖、3 项研究桥、3 项 GPU 扩展、1 项真实注册证据；基础专项另跳过 scipy。
- wheel / sdist 构建通过，基础运行依赖仅 NumPy / PyYAML；9 项算法发现和 pip check 通过，未安装或导入 Torch。基础测试额外安装 pytest 及其依赖。没有修改原始数据或真实机器人配置。验证与源码摘要见 validation.json（本地留存：evidence/numeric_gaps_20261026/validation.json），包信息见 package.json（本地留存：evidence/numeric_gaps_20261026/package.json）。本轮仅覆盖上述两类缺口，不代表 Dayan 全部指标迁入或真实机器人效果验收完成。

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
