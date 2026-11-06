# 操作指南

[返回主页](../../README.zh-CN.md) · [English](index.en.md)

数据接入、质量检查、筛选导出、工作流与插件的操作步骤。

| 任务 | 指南 | 前提 |
|---|---|---|
| 接入记录并配置机器人 | [接入数据并配置机器人](DATA_AND_ROBOT.zh-CN.md) | 已安装环境；已确认格式与字段语义 |
| 选择并执行适用的检查 | [推荐、选择并运行算法](RUN_AND_SELECT.zh-CN.md) | 机器人 YAML 已通过 `edc robot check` |
| 复制或链接选中的数据 | [按报告筛选并导出数据](SELECTION.md) | 已有 `report.json`；输出目录独立于源数据 |
| 编排质量、编辑和增强阶段 | [编排并运行质量工作流](WORKFLOWS.md) | 质量算法可独立运行；已明确阶段与挂起规则 |
| 接入外部质量算法 | [接入新质量算法](ALGORITHM_PLUGIN.zh-CN.md) | 已安装 `edc`；已准备样例数据 |
| 接入数据格式 | [接入新的数据格式读取器](READER_PLUGIN.zh-CN.md) | 已明确字段、时间来源与轨迹边界 |

## 相关文档

- [教程](../tutorial/QUICKSTART.zh-CN.md)：安装与完整运行。
- [参考](../reference/index.md)：字段、参数、接口与命令。
- [说明](../explanation/index.md)：框架设计与研究依据。
- [项目与维护](../project/DOCUMENTATION.md)：贡献流程与文档维护。

```{toctree}
:hidden:
:maxdepth: 1

接入数据并配置机器人 <DATA_AND_ROBOT.zh-CN>
推荐、选择并运行算法 <RUN_AND_SELECT.zh-CN>
按报告筛选并导出数据 <SELECTION>
编排并运行质量工作流 <WORKFLOWS>
接入新质量算法 <ALGORITHM_PLUGIN.zh-CN>
接入新的数据格式读取器 <READER_PLUGIN.zh-CN>
```
