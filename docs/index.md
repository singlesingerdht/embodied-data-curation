---
html_theme.sidebar_secondary.remove: true
---

# EDC · 具身数据整理框架

:::{container} edc-intro
让机器人数据，从“可用”走向“值得学”。
:::

**EDC = Embodied Data Curation**。面向具身数据使用者与算法开发者，
通过数据读取、机器人配置、质量检查、报告与证据，为数据选择提供可追溯的依据。

## 框架概览

:::{figure} assets/framework-overview.zh-CN.svg
:alt: EDC 总览：以质量证据、任务进展和策略反馈支持数据整理
:target: _images/framework-overview.zh-CN.svg
:figclass: edc-overview

点击图片查看大图。
:::

- **跨机器人复用**：机器人构型不同，但运动、感知和时间等数据具有共通含义，因此可在统一表示上复用检查方法。
- **自动化**：专家示教提供可学习的判断信号，任务与训练反馈提供可比较的选择结果，因此评估与筛选具备逐步自动化的条件。
- **有理有据**：专家经验提供任务参照，模型反馈提供学习参照，因此可以依据数据与用户目标的关系解释取舍，并检验选择效果。

相关论文已为部分机制提供支撑：**STEAM** 支持从示教中学习自动评估，**RoboDrop** 支持依据模型学习信号进行数据筛选。

当前可完成数据质量检查与筛选导出；任务价值和策略价值方法将逐步接入。
[从质量检查到自主数据整理](explanation/QUALITY_VALIDATION.zh-CN.md)。

## 从这里开始

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} 数据使用者

检查数据、查看问题与证据，并导出选中的记录。

[快速开始](tutorial/QUICKSTART.zh-CN.md) · [数据与机器人配置](how-to/DATA_AND_ROBOT.zh-CN.md)

[算法运行](how-to/RUN_AND_SELECT.zh-CN.md) · [筛选与导出](how-to/SELECTION.md)
:::

:::{grid-item-card} 算法开发者

复用数据读取、检查与报告能力，接入算法或数据格式。

[算法扩展](how-to/ALGORITHM_PLUGIN.zh-CN.md) · [读取器扩展](how-to/READER_PLUGIN.zh-CN.md)

[接口与输出参考](reference/OUTPUTS.zh-CN.md)
:::

::::

## 文档导航

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} 教程
:link: tutorial/QUICKSTART.zh-CN
:link-type: doc

安装、准备数据并生成首份报告。
:::

:::{grid-item-card} 操作指南
:link: how-to/index
:link-type: doc

接入数据、运行算法、导出数据、开发插件。
:::

:::{grid-item-card} 参考
:link: reference/index
:link-type: doc

查阅数据格式、配置字段、算法参数与接口。
:::

:::{grid-item-card} 说明
:link: explanation/index
:link-type: doc

了解框架设计、质量含义、研究依据与验证。
:::

::::

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
