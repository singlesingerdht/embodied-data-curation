# How-to Guides

[Home](../../README.md) · [简体中文](index.md)

Procedures for data integration, quality checks, selection, and plugins.

| Task | Guide | Prerequisites |
|---|---|---|
| Connect a recording and configure its robot | [Data and Robot Configuration](DATA_AND_ROBOT.md) | Installed environment; known format and field semantics |
| Select and execute applicable checks | [Run Algorithms](RUN_AND_SELECT.md) | Robot YAML validated with `edc robot check` |
| Copy or link selected data | [Selection and Export](SELECTION.en.md) | `report.json`; output directory outside source data |
| Add an external quality algorithm | [Algorithm Plugins](ALGORITHM_PLUGIN.md) | Installed `edc`; sample data |
| Add a data format | [Reader Plugins](READER_PLUGIN.md) | Defined fields, time sources, and episode boundaries |

## Related documentation

- [Tutorial](../tutorial/QUICKSTART.md): installation and a complete run.
- [Reference](../reference/index.en.md): fields, parameters, interfaces, and commands.
- [Explanation](../explanation/index.en.md): framework design and research foundations.
- [Project and maintenance](../project/DOCUMENTATION.md): contribution and documentation maintenance.

```{toctree}
:hidden:
:maxdepth: 1

Data and Robot Configuration <DATA_AND_ROBOT>
Run Algorithms <RUN_AND_SELECT>
Selection and Export <SELECTION.en>
Algorithm Plugins <ALGORITHM_PLUGIN>
Reader Plugins <READER_PLUGIN>
```
