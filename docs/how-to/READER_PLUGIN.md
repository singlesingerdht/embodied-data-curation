# Reader Plugins

[Home](../../README.md) · [简体中文](READER_PLUGIN.zh-CN.md) · [Output objects and interface reference](../reference/OUTPUTS.md)

Reader plugins expose a data format as Dataset → Episode → Channel through the `edc.readers` entry point.
The contract for the reader interface is in the [output objects and interface reference](../reference/OUTPUTS.md#readers); the data model and its boundaries are in [framework structure and design choices](../explanation/ARCHITECTURE.md).

A robot using an existing format usually requires only a configuration; see [Data and Robot Configuration](DATA_AND_ROBOT.md).

## 1. Start from the template

Copy [`manifest_reader.py`](../../examples/plugins/reader/manifest_reader.py) and its [`pyproject.toml`](../../examples/plugins/reader/pyproject.toml) into an external plugin package. Set the package metadata and `edc.readers` entry point to the reader implementation. The template reads a custom manifest and uses internal data-model classes; concrete `Dataset` and `Episode` constructors are not currently exported from `edc`. Public dataset access uses `from edc import open_dataset`.

## 2. Implement the two entry points

- `can_open(path)`: state only whether this reader claims the source; do not read all content.
- `open_dataset(path, ...)`: return the common data model, preserving Dataset → Episode → Channel.

Implementation requirements:

1. Preserve episode ID, source, and selector; never derive episode count from file count.
2. Distinguish acquisition, container, playback, synthetic, and unknown time; record unit conversion, clock mapping, and resampling separately.
3. Preserve unknown semantics and units as unknown; do not infer them from field names or normalization.
4. Verify numeric, image, time, and provenance reads against declarations.

## 3. Install and validate

```bash
pip install /path/to/reader-plugin
```

Open a representative source with the public API and a matching robot configuration, then run the applicable checks using [Run Algorithms](RUN_AND_SELECT.md). Confirm that preflight and execution discover the installed reader. Validation covers:

- available, missing, corrupt, and failed inputs each have an explicit outcome;
- multi-episode boundaries come from metadata or selectors, not file counts;
- provenance and source records remain traceable, with sources read-only;
- the reader never treats playback or synthetic time as acquisition time.

See [contributing](../../CONTRIBUTING.md) for relevant tests and packaging checks.

## Related pages

- [Output objects and interface reference](../reference/OUTPUTS.md#readers): reader contract.
- [Data formats and robot configuration reference](../reference/DATA_AND_ROBOTS.md): existing formats and configurations.
- [Framework structure and design choices](../explanation/ARCHITECTURE.md): why data is organized as Dataset → Episode → Channel.
