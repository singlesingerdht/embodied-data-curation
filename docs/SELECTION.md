# 按报告生成数据副本 / Copy data selected by a report

[中文主页](../README.zh-CN.md) · [English home](../README.md) · [工作流 / Workflows (Chinese)](WORKFLOWS.md)

`edc regenerate` 已提供按 batch 报告复制／链接数据的入口。它保留原判断，不重算算法，不修复数据；完整的实际使用流程仍在路线图中。

`edc regenerate` copies or links data selected from a batch report. It preserves original decisions without recomputing checks or repairing data. A complete practical selection workflow remains on the roadmap.

## 命令 / Command

输入为 `edc batch --output /path/to/report.html` 同时写出的 `/path/to/report.json`。

Use the JSON written alongside the HTML by `edc batch --output /path/to/report.html`.

```bash
edc regenerate /path/to/report.json --output /path/to/selected \
  [--mode copy|link] [--include keep,review] [--profile /path/to/robot.yaml] [--preview]
```

方括号表示可选参数，实际执行时去掉方括号。 / Square brackets mark optional arguments; omit the brackets when running.

```bash
# Preview selection without writing data / 只预览
edc regenerate /path/to/results/report.json --output /path/to/selected --preview

# Copy the default keep subset / 默认复制 keep 子集
edc regenerate /path/to/results/report.json --output /path/to/selected

# Explicit additional decisions / 显式选择更多状态
edc regenerate /path/to/results/report.json --output /path/to/selected-review --include keep,review

# Links depend on retained sources / 链接依赖原数据持续存在
edc regenerate /path/to/results/report.json --output /path/to/selected-links --mode link
```

## 参数与选择条件 / Options and selection

| 参数 / Argument | 默认 / Default | 说明 / Meaning |
|---|---|---|
| 报告路径 / Report path | 必填 / Required | batch JSON，包含 `results[]` / Batch JSON containing `results[]` |
| `--output` | 必填 / Required | 不存在或为空的目录，不能位于源数据目录内 / A new or empty directory outside source data |
| `--mode` | `copy` | 复制可独立使用；软链接要求源数据保留 / Copy is independent; links require retained sources |
| `--include` | `keep` | 逗号分隔的原始结论值，不改变结论 / Comma-separated original decisions; decisions remain unchanged |
| `--profile` | 不指定 / Unset | 解析轨迹身份所需的机器人 YAML，按需提供 / Optional robot YAML for episode identity resolution |
| `--preview` | 关闭 / Off | 打印选择数量及源、目标、操作，不写数据 / Show selection count, source, destination and operation without writing |

报告中的原始结论值为 `keep`、`salvage`、`review`、`unassessed`、`exclude`、`failed`。默认仅选择 `keep`；显式选择其他值不会将其改为通过。筛选按字面值匹配，未识别的值不会报错，通常不会匹配数据。

Original report decisions are `keep`, `salvage`, `review`, `unassessed`, `exclude`, and `failed`. Only `keep` is selected by default. Including another decision does not make it pass. Values are matched literally; an unrecognized value does not raise an error and normally matches no data.

## 输出 / Output

```text
selected/
├── manifest.json   # 来源、选择条件和结果 / Provenance, selection and results
├── dataset.yaml    # 仅复制模式 / Copy mode only
└── ...             # 数据副本或链接 / Copied data or links
```

`manifest.json`（`dqs_regenerate_manifest_v1`）保存报告来源、筛选条件、选中条数、原判断、源→目标映射、episode ID／format／selector、操作和失败原因。复制后通过框架回读，核对轨迹集合与预期一致。batch 的保留建议不代替完整检查或工作流放行。

`manifest.json` (`dqs_regenerate_manifest_v1`) records report provenance, selection criteria/counts, original decisions, source-to-output mappings, episode IDs/formats/selectors, operations and errors. Copied data is reopened to verify the expected episode set. A batch keep suggestion does not substitute for full checks or workflow release requirements.

## 使用边界与退出码 / Boundaries and exit codes

| 情况 / Case | 行为 / Behavior |
|---|---|
| 独立轨迹目录、MCAP 或 rosbag / Independent episode directory, MCAP or rosbag | 整体复制或链接 / Copy or link the whole source |
| 一个 LeRobot、Parquet 或 HDF5 源包含多条轨迹 / Multiple episodes sharing one LeRobot, Parquet or HDF5 source | 复制容器一次，由 `dataset.yaml` 的 selector 保留选择范围；链接模式报错 / Copy the container once and retain selectors in `dataset.yaml`; link mode rejects this case |
| 一个目录内有多个独立轨迹子目录 / One directory containing multiple independent episode directories | 记录失败，需按轨迹导出 / Record failure; export individual episodes |
| 零条匹配 / No matches | 不创建目录，退出码0 / No output directory; exit 0 |
| 同名、缺失源、源不存在或名称冲突 / Duplicate names, missing source, nonexistent source or reserved-name conflict | 记入清单，继续其他条目，退出码1 / Record failures, continue other entries; exit 1 |
| 输出目录非空或位于源内，报告不存在或格式错误 / Nonempty or nested output, missing or invalid report | 报错且不写数据，退出码2 / Reject without writing; exit 2 |

原始数据始终只读。 / Sources remain read-only.

价值评估、编辑与增强算法尚需逐项接入。已有有限阶段编排、空阶段跳过、来源校验和质量复检接口，以及明确标注的合成示例见[工作流说明](WORKFLOWS.md)。

Value, editing, and augmentation algorithms need separate integration. Existing finite stages, optional empty stages, provenance validation, rechecks, and synthetic examples are documented in [WORKFLOWS.md (Chinese)](WORKFLOWS.md).
