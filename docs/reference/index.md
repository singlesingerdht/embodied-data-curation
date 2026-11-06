# 参考

[返回主页](../../README.zh-CN.md) · [English](index.en.md)

数据格式、配置字段、算法、公共接口与命令的定义。

## 按问题选择

| 想知道什么 | 页面 | 主要内容 |
|---|---|---|
| 支持哪些数据格式？已有哪份机器人配置？YAML 字段怎么填？单位与时间怎么声明？ | [数据格式与机器人配置参考](DATA_AND_ROBOTS.zh-CN.md) | 格式表、已有配置表、字段表、检查范围、单位与时间来源 |
| 某项检查需要什么输入？有哪些参数？结果怎么判？边界是什么？ | [内置质量算法参考](ALGORITHMS.zh-CN.md) | 九项算法的输入、参数、输出与边界 |
| `Item`、`AlgorithmResult` 和 `episode` 有哪些成员？插件如何注册？ | [输出对象与接口参考](OUTPUTS.zh-CN.md) | 接口类型、数据视图、输出对象、运行记录、读取器契约 |
| `edc` 有哪些命令和选项？退出码表示什么？ | [命令行参考](CLI.md) | 命令表、常用选项、环境变量、退出码 |
| 机械臂范围、时间差证据、画面联合复核怎么配置？ | [检查范围与证据参数参考](EVIDENCE_AND_SCOPE.md) | `arms` 选择、`record_header_deviation_threshold_ms`、`freeze_motion_review` 与证据字段 |

## 相关区域

- [教程](../tutorial/QUICKSTART.zh-CN.md)：安装与完整运行。
- [操作指南](../how-to/index.md)：按任务分步完成。
- [说明](../explanation/index.md)：背景、边界与设计取舍。

```{toctree}
:hidden:
:maxdepth: 1

数据格式与机器人配置参考 <DATA_AND_ROBOTS.zh-CN>
内置质量算法参考 <ALGORITHMS.zh-CN>
输出对象与接口参考 <OUTPUTS.zh-CN>
命令行参考 <CLI>
检查范围与证据参数参考 <EVIDENCE_AND_SCOPE>
```
