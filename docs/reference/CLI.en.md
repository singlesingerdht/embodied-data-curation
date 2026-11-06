# EDC Command-Line Reference

[Home](../../README.md) · [简体中文](CLI.en.md) · [How-to guides](../how-to/index.en.md)

Commands and common options are listed below. The installed `edc <command> --help` and source code are authoritative; follow the linked guides for procedures.

## Commands

| Command | Purpose | Guide |
|---|---|---|
| `edc channels <data>` | List channels found by the reader | [Connect data and configure a robot](../how-to/DATA_AND_ROBOT.md) |
| `edc robot check --robot <yaml> --data <data>` | Verify that fields declared in robot YAML can be read | [Connect data and configure a robot](../how-to/DATA_AND_ROBOT.md) |
| `edc robot urdf --urdf <file>` | List URDF chains and joints | [Connect data and configure a robot](../how-to/DATA_AND_ROBOT.md#urdf) |
| `edc robot urdf --robot <yaml> --data <data1> [data2]` | Check joint directions and coordinate frames in the YAML's URDF section | [Connect data and configure a robot](../how-to/DATA_AND_ROBOT.md#urdf) |
| `edc batch <inputs...> --robot <yaml>` | Run the registered quality suite on each episode and generate one HTML report | [Recommend, select, and run algorithms](../how-to/RUN_AND_SELECT.md) |
| `edc run <data> --output <directory>` | Run on one input with a complete output directory; optionally select algorithms | [Recommend, select, and run algorithms](../how-to/RUN_AND_SELECT.md) |
| `edc algorithms` | List registered quality algorithms, both built-in and installed | [Recommend, select, and run algorithms](../how-to/RUN_AND_SELECT.md) |
| `edc regenerate <report.json> --output <directory>` | Copy or link selected data using a batch report | [Select and export data from a report](../how-to/SELECTION.en.md) |
| `edc workflow check <config>` | Validate stage order, methods, and mandatory rechecks | [Orchestrate and run quality workflows](../how-to/WORKFLOWS.en.md) |
| `edc workflow run <config> --output <directory>` | Execute configured stages and save the release decision | [Orchestrate and run quality workflows](../how-to/WORKFLOWS.en.md) |

## Common options

### `edc batch`

```bash
edc batch <inputs...> (--robot <yaml> | --profile <yaml>) \
  [--jobs <N>] [--output <report.html>] [--keep <directory>] [--tmp <directory>]
```

| Option | Description |
|---|---|
| `--robot` / `--profile` | Robot configuration; exactly one is required |
| `--jobs` | Number of concurrent episodes; default 16, recommended 1 for a first run |
| `--output` | Report file; generated from robot name and time when omitted |
| `--keep` | Save complete outputs and evidence for each episode; the directory must be new |
| `--tmp` | Temporary directory; defaults to the system temporary directory |

`batch` does not accept `--algorithm`; use `edc run` to run a subset of algorithms.

### `edc run`

```bash
edc run <data> [--robot <yaml> | --profile <yaml>] --output <directory> \
  [--algorithm <algorithm_id> ...] [--episode <K>] [--backend auto|official|compat] [--no-html]
```

| Option | Description |
|---|---|
| `--algorithm` | Run only the specified algorithm; repeatable, preserving command-line order |
| `--episode` | Run only episode K in the input, indexed from 0 |
| `--backend` | Reader backend; default `auto` |
| `--no-html` | Skip the per-run HTML report; retain JSON and evidence |

Without `--algorithm`, the command attempts fully and partially runnable quality methods and records why other methods cannot run.

### `edc regenerate`

See [select and export data from a report](../how-to/SELECTION.en.md) for all options, selection conditions, and exit codes.

### `edc workflow`

```bash
edc workflow check <workflow.yaml>
edc workflow run <workflow.yaml> --output <directory> [--resume]
```

`check` validates configuration only; `run` executes stages. `--resume` resumes from saved state. See [orchestrate and run quality workflows](../how-to/WORKFLOWS.en.md) for stage fields.

## Environment variables

| Variable | Purpose |
|---|---|
| `EDC_PLUGINS` | Plugin folders to run directly, separated by the platform path separator (`:` on Linux/macOS); affects only the current command |

Example:

```bash
EDC_PLUGINS=examples/plugins/algorithm/joint_jump edc algorithms
```

## Exit codes

| Condition | Exit code |
|---|---|
| Command completes; `run` has no processing errors; `workflow run` has `delivery_status: ready` | 0 |
| `batch` has a failed episode; `run` has a `processing_error`; `workflow run` is not `ready`; a `robot` check finds a mismatch or fails | 1 |
| Uncaught exception or argument error; invalid output directory or report for `regenerate` | 2 (see [selection and export](../how-to/SELECTION.en.md) for complete `regenerate` rules) |

Exit code 0 means the command completed; it does not mean the data passed quality checks. Use individual report judgments to assess quality.

## Known limitations

- There is no standalone recommendation CLI or `edc config export` command.
