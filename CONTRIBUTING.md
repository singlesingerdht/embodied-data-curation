# 参与开发

本仓库是具身数据整理框架的私有协作基线。公共入口、CPU 方法与适配以此库为维护源；研究仓库保留 GPU 方法、真实数据环境和历史实验。请不要在两边长期手工复制同一核心。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[formats,test]'
pip install -e examples/plugins/algorithm
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/edc -q
python -m build
```

完整官方 LeRobot 验收需要额外安装 `.[lerobot]`（Python≥3.12）；基础环境允许该组测试明确跳过，不得把跳过计作通过。

使用 `codex/<task>` 或个人任务分支并通过 PR 提交。PR 说明触发问题、最终行为、验证及兼容边界；不要混入数据、权重、wheel 或实验输出。

## 契约要求

- 自有内部代码直接导入 `edc` 下所属模块；外部使用者／插件通过公共接口接入。新公共函数由包顶层 `edc` 导出，保持薄入口。
- 数据遵守 Dataset → Episode → Channel，metadata／映射确定轨迹边界，不按文件数猜。
- 不猜单位、物理量、指令／反馈、左右、clock；SI 换算与时钟映射、采样对齐分别记录。
- 算法使用 `AlgorithmSpec` 和 run；模型延迟加载；外部 ID `team__algorithm`。新算法不要修改核心 ID 分发表。
- 保留原评分、事件、分布或估值，不生成跨算法总分。处理失败与数据不合格独立；不完整评估不得放行。
- 编辑／增强生成新副本和明确来源映射；变换校验后才交接，下游仍需质量复检。
- 对契约、边界、插件、失败隔离或判断语义的变更添加有意义测试；低风险文案不必硬加测试。
- 本次迁移不改原 CPU 方法、评分公式和阈值。若将来要改变方法，应单独提 PR、解释方法版本与历史结果不可直接比较的边界。

## 新插件

参考 [插件模板](examples/plugins/algorithm)（新算法继承 `edc.algorithms.base.QualityAlgorithm`，见 `joint_jump.py`）、[读取器模板](examples/plugins/reader)、[使用说明](README.zh-CN.md)。先通过独立安装、可用／缺输入／缺依赖／失败隔离测试，再登记支持范围。

质量、价值、编辑、增强类别独立；dataset 粒度需 `preflight_dataset(dataset, params)`，对当前子集调用一次。真实价值方法必须保留原输出，不以盘点数量代替实验收益。

## 提交前

运行相关测试、打包及源码外安装检查。核对新依赖、代码来源、数据及模型许可；未知许可保持待确认。完整规则见根目录 [AGENTS.md](AGENTS.md)。
