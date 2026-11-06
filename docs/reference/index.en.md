# Reference

[Home](../../README.md) · [简体中文](index.md)

Supported formats, configuration fields, algorithms, public interfaces, and commands.

## Choose by question

| Topic | Page | Main content |
|---|---|---|
| Which formats are supported? Which robot configurations exist? How are YAML fields and units/time declared? | [Data formats and robot configuration reference](DATA_AND_ROBOTS.md) | Format table, existing configurations, field table, run scope, units and time sources |
| What inputs does a check need? Which parameters does it take? How are results decided? What are the limits? | [Built-in quality algorithm reference](ALGORITHMS.md) | Inputs, parameters, outputs, and limits of the nine methods |
| What members do `Item`, `AlgorithmResult`, and `episode` expose? How do plugins register? | [Output objects and interface reference](OUTPUTS.md) | Interface types, data view, output objects, run records, reader contract |
| Which `edc` commands and options exist? What do exit codes mean? | [Command-line reference](CLI.en.md) | Commands, common options, environment variables, exit codes |
| How are arm scope, record-header evidence, and joint freeze evidence configured? | [Scope and evidence reference](EVIDENCE_AND_SCOPE.en.md) | `arms` selection, `record_header_deviation_threshold_ms`, `freeze_motion_review`, and evidence fields |

## Related areas

- [Tutorial](../tutorial/QUICKSTART.md): one complete run.
- [How-to guides](../how-to/index.en.md): task-oriented procedures.
- [Explanation](../explanation/index.en.md): background, limits, and design choices.

```{toctree}
:hidden:
:maxdepth: 1

Data formats and robot configurations <DATA_AND_ROBOTS>
Built-in quality algorithms <ALGORITHMS>
Output objects and interfaces <OUTPUTS>
Command-line reference <CLI.en>
Scope and evidence <EVIDENCE_AND_SCOPE.en>
```
