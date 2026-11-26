# Model environments and dependencies

[中文](MODEL_ENVIRONMENTS.zh-CN.md) · [Value algorithms](VALUE_ALGORITHMS.md)

EDC provides one command interface for optional model environments. The lightweight base handles data, execution and results. Model packages are installed in new Python environments selected with each algorithm's `python` parameter. Compatible components may share a worker; OpenPI keeps its training stack and modified Transformers in a separate environment.

The OpenPI environment profile supports separately supplied valuation implementations; preparing an environment does not register a valuation algorithm.

## Profiles

| Profile | Components | Core versions | Scope |
|---|---|---|---|
| `vision` | `progress`, `initial-state`, `scene`, individually or together | Python 3.12, Torch 2.8.0, TorchVision 0.23.0, Transformers 4.57.1, NumPy 1.26.4 | GRM adds vLLM 0.11.0; `scene` (initial placement, hand intervention) uses a SAM2 source wheel |
| `openpi` | `shapley` | Python 3.12, Torch 2.7.1, Transformers 4.53.2, JAX 0.5.3, Flax 0.10.2 | Supply OpenPI, openpi-client and LeRobot wheels; apply that OpenPI's bundled Transformers replacements |

The `scene` component serves both initial placement and hand intervention, which share one local Qwen3-VL. Model paths go under `checks` in the robot yaml; see the [value algorithm guide](VALUE_ALGORITHMS.md#initial-placement-assessment).

These Linux x86_64 recipes do not certify every model combination. Installation success proves neither model accuracy nor complete attribution. Model weights are never downloaded by this installer. Some native SAM3 builders allocate CUDA position-encoding tensors even when CPU is selected; successful imports do not certify GPU-free inference. Verify the supplied source separately.

Native SAM3 requires NumPy below 2. The vision recipe constrains OpenCV to 4.11 to avoid bringing in a NumPy 2 requirement. OpenPI cannot be mixed with the vision components: its model depends on a particular modified Transformers release.

## Inspect and install

All paths below are **placeholders**. `--python` selects the Python 3.12 interpreter used to create the environment. `--edc-wheel` supplies the same framework build as the caller. The destination must not exist.

```bash
edc env list
edc env plan vision --with progress --with initial-state

edc env create vision --with progress --with initial-state \
  --python /path/to/python3.12 \
  --venv /path/to/envs/vision \
  --edc-wheel /path/to/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source sam3=/path/to/sam3.whl

edc env check --venv /path/to/envs/vision --imports
```

For progress alone, select only `--with progress`; no SAM3 wheel is required. Build source wheels using each project's normal build process in a separate build directory. Supply their actual complete filenames; the installer does not accept mutable source directories or substitute another upstream project.

```bash
edc env create openpi --with shapley \
  --python /path/to/python3.12 \
  --venv /path/to/envs/openpi \
  --edc-wheel /path/to/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source openpi=/path/to/openpi.whl \
  --source openpi-client=/path/to/openpi_client.whl \
  --source lerobot=/path/to/lerobot.whl
```

Use OpenPI sources matching the valuation method and LeRobot revision `0cf864870cf29f4738d3ade893e6fd13fbd7cdb5`. The installer records supplied wheel hashes; it does not prove their upstream revisions or licenses. Missing OpenPI replacements, dependency conflicts and import failures fail installation checks. Replacements affect only the new OpenPI environment, recording original and replacement hashes.

## Run through EDC

Set `checks.<algorithm ID>.python` in the robot YAML. Supply the remaining parameters from the [value algorithm reference](VALUE_ALGORITHMS.md):

```yaml
checks:
  Initial_State_Distribution:
    python: /path/to/envs/vision/bin/python
    # Also supply channel, checkpoint, comparison_space, objects, etc.
  Progress_Stagnation_Attribution:
    python: /path/to/envs/vision/bin/python
    # Also supply views, model, task, subtasks or segmentation, physical_signals.
```

```bash
edc run /path/to/data --robot /path/to/robot.yaml \
  --algorithm Progress_Stagnation_Attribution \
  --output /path/to/new-results --no-html
```

Initial distribution and progress attribution support a separate process selected through `python`. Initial distribution keeps in-process execution when omitted. With a worker, EDC saves lossless RGB frames and original mappings; the worker uses the same detection and statistics code. The caller still needs data-format libraries and Pillow for image preparation. Worker imports do not inherit external Python search paths from environment variables.

## Evidence and checks

`edc-environment/` stores the recipe, supplied wheel SHA-256 hashes, pip installation report, installed version snapshot, `freeze.txt`, installation log and check result. Core versions are pinned; remaining transitive dependencies are resolved and recorded at installation. A recipe is not a complete cross-platform lockfile.

`edc env check` verifies isolation, dependency consistency, installed versions and EDC source drift. `--imports` also imports selected libraries without loading weights or running inference. Success means installation checks passed. After updating EDC, create a new environment from the new wheel and change `python`; retain the old one for rollback. Existing destinations are never overwritten. Failed installations retain their logs; retry with a new directory after correcting the cause.

Execution separately verifies matching caller/worker method sources and saves model/configuration provenance before model initialization. GRM respects checkpoint-declared output-head sharing without patching global Transformers classes. The known incompatible vLLM 0.11 / Transformers 5 combination fails dependency checks. Environment management does not change scoring, clocks, quality thresholds or assessment completion rules.

Initial placement and hand intervention use `--with scene` with a SAM2 source wheel; download Grounding DINO, SAM2 and Qwen3-VL weights separately.
