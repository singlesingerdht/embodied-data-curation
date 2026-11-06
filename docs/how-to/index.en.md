# How-to Guides

[Home](../../README.md) · [简体中文](index.md)

Procedures for data integration, quality checks, selection, workflows, and plugins.

| Task | Guide | Prerequisites |
|---|---|---|
| Connect a recording and configure its robot | [Connect data and configure a robot](DATA_AND_ROBOT.md) | Installed environment; known format and field semantics |
| Select and execute applicable checks | [Recommend, select, and run algorithms](RUN_AND_SELECT.md) | Robot YAML validated with `edc robot check` |
| Copy or link selected data | [Select and export data by report](SELECTION.en.md) | `report.json`; output directory outside source data |
| Compose quality, editing, and augmentation stages | [Orchestrate and run a quality workflow](WORKFLOWS.en.md) | Working quality algorithms; stage and suspension rules |
| Add an external quality algorithm | [Add a new quality algorithm](ALGORITHM_PLUGIN.md) | Installed `edc`; sample data |
| Add a data format | [Add a reader plugin](READER_PLUGIN.md) | Defined fields, time sources, and episode boundaries |

## Related documentation

- [Tutorial](../tutorial/QUICKSTART.md): installation and a complete run.
- [Reference](../reference/index.en.md): fields, parameters, interfaces, and commands.
- [Explanation](../explanation/index.en.md): framework design and research foundations.
- [Project and maintenance](../project/DOCUMENTATION.md): contribution and documentation maintenance.

```{toctree}
:hidden:
:maxdepth: 1

Connect data and configure a robot <DATA_AND_ROBOT>
Recommend, select, and run algorithms <RUN_AND_SELECT>
Select and export data by report <SELECTION.en>
Orchestrate and run a quality workflow <WORKFLOWS.en>
Add a new quality algorithm <ALGORITHM_PLUGIN>
Add a reader plugin <READER_PLUGIN>
```
