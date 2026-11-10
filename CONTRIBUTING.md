# 参与开发

欢迎贡献数据读取器、算法、配置示例和文档。请先明确支持范围与输入条件，再通过独立验证提交修改。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[formats,test]'
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
- 修改算法方法、评分或阈值时，应单独提 PR，解释方法版本及结果比较边界。

## 新插件

参考 [插件模板](examples/plugins/algorithm)（新算法继承 `edc.algorithms.base.QualityAlgorithm`，见 `joint_jump/`）、[读取器模板](examples/plugins/reader)、[输出基类与完整示例](docs/how-to/ALGORITHM_PLUGIN.zh-CN.md)。先通过独立安装、可用／缺输入／缺依赖／失败隔离测试，再登记支持范围。

质量、价值、编辑、增强类别独立；dataset 粒度需 `preflight_dataset(dataset, params)`，对当前子集调用一次。真实价值方法必须保留原输出，不以盘点数量代替实验收益。

## 算法接入与内化

自有算法按质量、价值、增强、编辑分别进入
`src/edc/algorithms/{quality,value,augmentation,editing}/algorithms/<算法名>/`。
外部算法先作为插件验证，再决定内化或独立维护；两者共用同一输入、执行和结果接口。
接入步骤、验证要求、ID切换、旧入口处置及回退见[算法接入与内化流程](docs/how-to/ALGORITHM_INTEGRATION.zh-CN.md)。

贡献应包含实现、输入输出与参数说明、依赖及来源许可、使用示例和测试。内化时验证方法等价性、
唯一注册和源码外安装。四类目录是归属约定；当前尚无四类统一自动发现，创建目录不等于接入完成。

README 面向使用者与算法开发者，说明用途、实际能力、安装运行、输入输出、使用边界和开发入口。中英文、算法和插件 README 均不记录团队迭代、内部待办或本机验证流水。

公开文档面向开发者说明使用方法与支持范围。内部计划、人员分工、执行日志和本机验收材料
保存在仓库外，不纳入源码、站点或发布包，也不从公开文档链接到这些材料。

## 提交前

运行相关测试、打包及源码外安装检查。核对新依赖、代码来源、数据及模型许可；未知许可保持待确认。完整规则见根目录 [AGENTS.md](AGENTS.md)。
