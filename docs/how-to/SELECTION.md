# 筛选与导出

[English](SELECTION.en.md)

`edc regenerate` 已提供按批量报告复制／链接数据的入口。它保留原判断，不重算算法，不修复数据。

## 命令

输入为 `edc batch --output /path/to/report.html` 同时写出的 `/path/to/report.json`。

```bash
edc regenerate /path/to/report.json --output /path/to/selected \
  [--mode copy|link] [--include keep,review] [--profile /path/to/robot.yaml] [--preview]
```

方括号表示可选参数，实际执行时去掉方括号。

```bash
# 只预览
edc regenerate /path/to/results/report.json --output /path/to/selected --preview

# 复制 keep 子集
edc regenerate /path/to/results/report.json --output /path/to/selected

# 选择更多状态
edc regenerate /path/to/results/report.json --output /path/to/selected-review --include keep,review

# 创建软链接
edc regenerate /path/to/results/report.json --output /path/to/selected-links --mode link
```

## 参数与选择条件

| 参数 | 默认 | 说明 |
|---|---|---|
| 报告路径 | 必填 | 批量报告 JSON，包含 `results[]` |
| `--output` | 必填 | 不存在或为空的目录，不能位于源数据目录内 |
| `--mode` | `copy` | 复制可独立使用；软链接要求源数据保留 |
| `--include` | `keep` | 逗号分隔的原始结论值，不改变结论 |
| `--profile` | 不指定 | 解析轨迹身份所需的机器人 YAML，按需提供 |
| `--preview` | 关闭 | 打印选择数量及源、目标、操作，不写数据 |

报告中的原始结论值为 `keep`、`salvage`、`review`、`unassessed`、`exclude`、`failed`。默认仅选择 `keep`；显式选择其他值不会将其改为通过。筛选按字面值匹配，未识别的值不会报错，通常不会匹配数据。

## 输出

```text
selected/
├── manifest.json   # 来源、选择条件和结果
├── dataset.yaml    # 仅复制模式
└── ...             # 数据副本或链接
```

`manifest.json`（`dqs_regenerate_manifest_v1`）保存报告来源、筛选条件、选中条数、原判断、源→目标映射、轨迹 ID、格式（`format`）与选择器（`selector`）、操作和失败原因。复制后通过框架回读，核对轨迹集合与预期一致。批量报告的保留建议不代替完整检查或工作流放行。

## 使用边界与退出码

| 情况 | 行为 |
|---|---|
| 独立轨迹目录、MCAP 或 rosbag | 整体复制或链接 |
| 一个 LeRobot、Parquet 或 HDF5 源包含多条轨迹 | 复制容器一次，由 `dataset.yaml` 的选择器 保留选择范围；链接模式报错 |
| 一个目录内有多个独立轨迹子目录 | 记录失败，需按轨迹导出 |
| 零条匹配 | 不创建目录，退出码0 |
| 同名、缺失源、源不存在或名称冲突 | 记入清单，继续其他条目，退出码1 |
| 输出目录非空或位于源内，报告不存在或格式错误 | 报错且不写数据，退出码2 |
