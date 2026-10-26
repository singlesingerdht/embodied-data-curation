# 当前验收状态

日期：**2026-10-26**。框架版本 **0.2.0a1**；Numeric_Check 适配 **ir-adapter-15**，其余 CPU 适配 **ir-adapter-14**。

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
