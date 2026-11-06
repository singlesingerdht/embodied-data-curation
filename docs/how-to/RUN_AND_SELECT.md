# Recommend, Select, and Run Algorithms

[Home](../../README.md) · [简体中文](RUN_AND_SELECT.zh-CN.md) · [Built-in algorithm reference](../reference/ALGORITHMS.md)

Inspect recommendations, select algorithms, and run quality checks. See the [algorithm reference](../reference/ALGORITHMS.md) for parameters.

<a id="selection"></a>
## 1. Inspect recommendations

Recommendations use inputs, unit eligibility, semantics, time, parameters, and dependencies. `available` means preflight requirements are met; `partial` identifies a runnable subset and missing scope; `unavailable` gives missing conditions. Preflight neither reads every payload nor certifies data quality.

```bash
python examples/quickstart/recommend.py /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml
```

The script prints the full `edc.recommend()` JSON. Each episode's `algorithms` includes `algorithm.id`, `availability`, `reason`, `channel_bindings`, and `parameters`; multi-instance methods also retain `instances`. Read the reason and scope, not just the algorithm name.

## 2. Select and execute

Select two algorithms:

```bash
edc run /path/to/dataset/episode.mcap \
  --robot configs/robots/x1pro_mcap.yaml \
  --algorithm Structure_Check --algorithm Numeric_Check \
  --output /path/to/results/selected
```

`run` preserves explicit order. Without `--algorithm`, it attempts full and partial quality methods and records unavailable reasons. `batch` runs the registered quality collection for each episode and can parallelize episodes; it has no `--algorithm` option:

```bash
edc batch /path/to/dataset \
  --robot /path/to/robot.yaml --output /path/to/results/report.html \
  --keep /path/to/results/details --jobs 1
```

`--keep` retains per-episode outputs and evidence in a new directory. `--jobs` controls episode concurrency (default 16; start with 1); `--tmp` sets temporary storage. `edc algorithms` lists registered algorithms. There is no standalone recommendation CLI or `edc config export` command. All options are listed in the [command-line reference](../reference/CLI.en.md).

### Use the same parameters (Python API)

Pass the same effective parameters to recommendation and execution. Compile a robot card; read a complete profile directly:

```python
from pathlib import Path
import yaml
import edc

robot = Path('configs/robots/x1pro_mcap.yaml').resolve()
config = yaml.safe_load(robot.read_text(encoding='utf-8'))
if 'robot' in config and 'schema_version' not in config:
    profile, params, _ = edc.compile_card(config, base=robot.parent)
else:
    profile, params = config, {}

with edc.open_dataset('/path/to/dataset/episode.mcap', profile=profile) as dataset:
    recommendation = edc.recommend(dataset, params=params)
    selected = ['Structure_Check', 'Numeric_Check']
    result = edc.run(dataset, output='/path/to/results/api',
                     algorithm_ids=selected,
                     params={k: v for k, v in params.items() if k in selected})
```

Card parameters belong only under `checks.<algorithm_id>`; no separate parameter file overrides them. After the yaml changes, `edc batch` asks for a new signature. Use `arms: [left]` and `channels` according to each method's schema; rerun recommendations with the same updated scope. [Declared robot versus run scope](../reference/DATA_AND_ROBOTS.md#scope)

<a id="results"></a>
## 3. Read the results

HTML provides an overall decision, episode overview, problems by algorithm, and episode details. Locate the affected episode and method, then inspect its reason. Full metrics and evidence are in JSON and outputs retained with `--keep`. See the [real MCAP reference result](../tutorial/QUICKSTART.md#results).

Item verdicts are PASS, REVIEW, SALVAGE, or EXCLUDE. Inapplicability and execution failure are distinct from quality judgments. A summary decision does not replace complete evaluation or workflow release conditions; unchecked inputs are not passes.

`run` exposes configuration, `records.jsonl`, `quality.json`, errors, and evidence under `_runs/<run_id>` through `catalog.json`. Native method metrics remain separate, with no combined algorithm/arm score. [Output objects](../reference/OUTPUTS.md)

## Next steps

- Copy or link the data a report selects: [select and export data by report](SELECTION.en.md).
- Chain stages into a rechecked flow: [orchestrate and run a quality workflow](WORKFLOWS.en.md).
- Tighten or skip a check: [built-in algorithm reference](../reference/ALGORITHMS.md) and [scope and evidence reference](../reference/EVIDENCE_AND_SCOPE.en.md).
