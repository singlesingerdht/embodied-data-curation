---
html_theme.sidebar_secondary.remove: true
---

# EDC · 具身数据整理框架

:::{container} edc-intro
让机器人数据，从“可用”走向“值得学”。
:::

**EDC = Embodied Data Curation**。面向具身数据使用者与算法开发者，
通过数据读取、机器人配置、质量检查、报告与证据，为数据选择提供可追溯的依据。

## 文档

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} 教程 · 快速开始
:link: tutorial/QUICKSTART.zh-CN
:link-type: doc

安装、准备数据并生成首份报告。
:::

:::{grid-item-card} 操作指南
:link: how-to/index
:link-type: doc

接入数据、运行算法、导出数据、编排工作流、开发插件。
:::

:::{grid-item-card} 参考
:link: reference/index
:link-type: doc

格式、字段、单位、算法参数、输出对象与命令行。
:::

:::{grid-item-card} 说明
:link: explanation/index
:link-type: doc

数据质量的含义、设计取舍、证据边界与研究依据。
:::

::::

## 入门路径

| 任务 | 入口 |
|---|---|
| 安装并运行完整示例 | [教程：快速开始](tutorial/QUICKSTART.zh-CN.md) |
| 接入采集记录 | [接入数据并配置机器人](how-to/DATA_AND_ROBOT.zh-CN.md) |
| 选择适用的检查 | [推荐、选择并运行算法](how-to/RUN_AND_SELECT.zh-CN.md) |
| 导出筛选结果 | [按报告筛选并导出数据](how-to/SELECTION.md) |
| 扩展算法或数据格式 | [接入新质量算法](how-to/ALGORITHM_PLUGIN.zh-CN.md) · [接入读取器](how-to/READER_PLUGIN.zh-CN.md) |
| 了解研究依据与验证范围 | [从质量检查到自主数据整理](explanation/QUALITY_VALIDATION.zh-CN.md) |

EDC 提供数据质量检查与有证据支持的筛选。任务价值与策略价值方法计划逐步接入，见[研究依据与验证安排](explanation/QUALITY_VALIDATION.zh-CN.md)。

## 框架概览

![EDC 总览：以质量证据、任务进展和策略反馈支持数据整理](assets/framework-overview.zh-CN.svg)

## 运行与使用结果

已有数据与匹配的机器人配置后，可执行：

```bash
edc batch /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --output /path/to/results/report.html --jobs 1
```

浏览器打开报告，按轨迹和算法查看结论、原因与证据；需要导出时使用
[`edc regenerate`](how-to/SELECTION.md)。运行成功、数据质量通过和工作流放行分别表达。
第一次运行请按[教程](tutorial/QUICKSTART.zh-CN.md)准备环境与数据。

```{toctree}
:hidden:
:maxdepth: 1

教程 <tutorial/QUICKSTART.zh-CN>
操作指南 <how-to/index>
参考 <reference/index>
说明 <explanation/index>
项目与维护 <project/DOCUMENTATION>
```

```{toctree}
:hidden:
:maxdepth: 1

English overview <index.en>
Tutorial <tutorial/QUICKSTART>
How-to guides <how-to/index.en>
Reference <reference/index.en>
Explanation <explanation/index.en>
```
