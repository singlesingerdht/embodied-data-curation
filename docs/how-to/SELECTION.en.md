# Selection and Export

[简体中文](SELECTION.md)

`edc regenerate` copies or links data selected from a batch report. It preserves original decisions without recomputing checks or repairing data.

## Command

Use the JSON written alongside the HTML by `edc batch --output /path/to/report.html`.

```bash
edc regenerate /path/to/report.json --output /path/to/selected \
  [--mode copy|link] [--include keep,review] [--profile /path/to/robot.yaml] [--preview]
```

Square brackets mark optional arguments; omit the brackets when running.

```bash
# Preview selection
edc regenerate /path/to/results/report.json --output /path/to/selected --preview

# Copy the keep subset
edc regenerate /path/to/results/report.json --output /path/to/selected

# Include additional decisions
edc regenerate /path/to/results/report.json --output /path/to/selected-review --include keep,review

# Create links to retained sources
edc regenerate /path/to/results/report.json --output /path/to/selected-links --mode link
```

## Options and selection

| Argument | Default | Meaning |
|---|---|---|
| Report path | Required | Batch JSON containing `results[]` |
| `--output` | Required | A new or empty directory outside source data |
| `--mode` | `copy` | Copy is independent; links require retained sources |
| `--include` | `keep` | Comma-separated original decisions; decisions remain unchanged |
| `--profile` | Unset | Optional robot YAML for episode identity resolution |
| `--preview` | Off | Show selection count, source, destination and operation without writing |

Original report decisions are `keep`, `salvage`, `review`, `unassessed`, `exclude`, and `failed`. Only `keep` is selected by default. Including another decision does not make it pass. Values are matched literally; an unrecognized value does not raise an error and normally matches no data.

## Output

```text
selected/
├── manifest.json   # Provenance, selection and results
├── dataset.yaml    # Copy mode only
└── ...             # Copied data or links
```

`manifest.json` (`dqs_regenerate_manifest_v1`) records report provenance, selection criteria/counts, original decisions, source-to-output mappings, episode IDs/formats/selectors, operations and errors. Copied data is reopened to verify the expected episode set. A batch keep suggestion does not substitute for full checks or workflow release requirements.

## Boundaries and exit codes

| Case | Behavior |
|---|---|
| Independent episode directory, MCAP or rosbag | Copy or link the whole source |
| Multiple episodes sharing one LeRobot, Parquet or HDF5 source | Copy the container once and retain selectors in `dataset.yaml`; link mode rejects this case |
| One directory containing multiple independent episode directories | Record failure; export individual episodes |
| No matches | No output directory; exit 0 |
| Duplicate names, missing source, nonexistent source or reserved-name conflict | Record failures, continue other entries; exit 1 |
| Nonempty or nested output, missing or invalid report | Reject without writing; exit 2 |
