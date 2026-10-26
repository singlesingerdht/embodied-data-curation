# 当前验收状态

日期：**2026-10-26**。框架版本 **0.1.0a3**，CPU 适配 **ir-adapter-13**。

## 本轮结果

| 项目 | 实测结果 | 依据 |
|---|---|---|
| 迁移基线 | 原工作区选定 213 文件，迁移前后源文件无变化；9 项方法目录 60 文件逐字节一致 | [migration.json](../validation/migration.json)、[基线](../migration/source-manifest.json) |
| CPU 行为对照 | 同一合成 ROS2 MCAP、同参数；9 项均 completed，原结果逐项一致 | [cpu-comparison.json](../validation/cpu-comparison.json)、[复现脚本](../../scripts/compare_cpu_methods.py) |
| 源码完整回归 | **465 passed / 21 skipped** | [测试摘要](../validation/tests.json) |
| 源码外 wheel 完整回归 | **465 passed / 21 skipped**，从 /tmp 运行，导入安装包 | [测试摘要](../validation/tests.json) |
| 最新工作流契约测试 | **37 passed**；最后一次代码变更后重验 | tests/dqs/test_workflows.py、test_workflow_inputs.py |
| 官方 LeRobot 专项 | 独立官方依赖环境 **20 passed**；这是单独运行，不与完整测试简单相加 | tests/dqs/test_lerobot_official.py、[摘要](../validation/tests.json) |
| 干净基础安装 | wheel 在无系统包、清除继承 PYTHONPATH 的 venv 中导入；仅 pip、NumPy、PyYAML、embodied-dqs；9 算法、未导入 Torch、pip check 通过 | [base-install.json](../validation/base-install.json) |
| 源码外完整示例 | 2→2→2→2→2→4 条阶段输入；全部阶段完成，恢复时六阶段均复用 | [wheel-workflow.json](../validation/wheel-workflow.json) |
| 打包 | wheel 与 sdist 构建通过；wheel 包含 143 Python 文件、9 CPU 方法及复位配置，不含 GPU 方法／权重／私有 wheel | [package.json](../validation/package.json) |
| GitHub 远端交付 | `main` 首次推送完成，本地与远端提交 `412a877` 一致，包含核心迁移及朋友验收指南 | [embodied-data-curation](https://github.com/singlesingerdht/embodied-data-curation) |

CPU 对照仅排除 `process_time_ms`、`adapter_elapsed_s` 和输出工件根路径，不排除评分、阈值、事件或判断。对照数据为新生成的合成数据，不冒充真实机器人重新验收。

21 项跳过包括：14 项需要本环境未安装的官方 LeRobot，3 项未迁研究价值桥，3 项 GPU 扩展，1 项未分发的真实注册证据。官方专项环境单独验证读取与转换；本轮没有把跳过算作通过。

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
- 本地校验使用 Python 3.13；3.10／3.12 矩阵已写 CI，本轮未在远端实际执行。
- 仓库级许可、算法／第三方／模型与真实数据分发范围仍待核查；不宣称已公开发布。

## 复现

```bash
pip install -e '.[formats,test]'
pip install -e examples/plugins/algorithm
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/dqs -q -rs
python -m build
python scripts/verify_migration.py --source /path/to/original-repository --output /tmp/migration.json
python scripts/compare_cpu_methods.py --source /path/to/original-repository --output /tmp/new-comparison
```

对照输出目录必须为全新目录。独立 wheel 的完整流程见 [README](../../README.zh-CN.md) 与 [WORKFLOWS](WORKFLOWS.md)。原数据、GPU／研究环境及历史真实报告仍在原仓库，本轮未修改。
