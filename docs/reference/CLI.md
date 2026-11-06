# EDC 命令行参考

[返回主页](../../README.zh-CN.md) · [操作指南](../how-to/index.md)

本页列出 `edc` 命令与选项。命令行为以安装版 `edc <命令> --help` 和源码为准；操作步骤见对应的操作指南页。

## 命令一览

| 命令 | 用途 | 操作指南 |
|---|---|---|
| `edc channels <数据>` | 列出读取器在数据中找到的通道 | [接入数据并配置机器人](../how-to/DATA_AND_ROBOT.zh-CN.md) |
| `edc robot check --robot <yaml> --data <数据>` | 核对机器人 YAML 声明的字段能否读出 | [接入数据并配置机器人](../how-to/DATA_AND_ROBOT.zh-CN.md) |
| `edc robot urdf --urdf <文件>` | 列出 URDF 的链与关节 | [接入数据并配置机器人](../how-to/DATA_AND_ROBOT.zh-CN.md#urdf) |
| `edc robot urdf --robot <yaml> --data <数据1> [数据2]` | 核对 YAML 中 URDF 段的关节方向与坐标系 | [接入数据并配置机器人](../how-to/DATA_AND_ROBOT.zh-CN.md#urdf) |
| `edc batch <输入...> --robot <yaml>` | 对每条轨迹运行已登记质量集合，生成一份 HTML | [推荐、选择并运行算法](../how-to/RUN_AND_SELECT.zh-CN.md) |
| `edc run <数据> --output <目录>` | 单份数据、完整输出目录；可指定算法 | [推荐、选择并运行算法](../how-to/RUN_AND_SELECT.zh-CN.md) |
| `edc algorithms` | 列出已注册的质量算法（内置与已安装） | [推荐、选择并运行算法](../how-to/RUN_AND_SELECT.zh-CN.md) |
| `edc regenerate <report.json> --output <目录>` | 按 batch 报告复制或链接选中数据 | [按报告筛选并导出数据](../how-to/SELECTION.md) |
| `edc workflow check <配置>` | 校验阶段顺序、方法与强制复检 | [编排并运行质量工作流](../how-to/WORKFLOWS.md) |
| `edc workflow run <配置> --output <目录>` | 执行配置的阶段并保存放行决定 | [编排并运行质量工作流](../how-to/WORKFLOWS.md) |

## 常用选项

### `edc batch`

```bash
edc batch <输入...> (--robot <yaml> | --profile <yaml>) \
  [--jobs <N>] [--output <报告.html>] [--keep <目录>] [--tmp <目录>]
```

| 选项 | 说明 |
|---|---|
| `--robot` / `--profile` | 机器人配置；二选一，必填 |
| `--jobs` | 同时运行的轨迹数，默认 16；首次使用建议 1 |
| `--output` | 报告文件；省略时按机器人名与时间生成 |
| `--keep` | 保存每条轨迹的完整输出与证据；目录须新建 |
| `--tmp` | 临时目录，默认使用系统临时目录 |

`batch` 不能使用 `--algorithm`；需要只跑部分算法时改用 `edc run`。

### `edc run`

```bash
edc run <数据> [--robot <yaml> | --profile <yaml>] --output <目录> \
  [--algorithm <算法ID> ...] [--episode <K>] [--backend auto|official|compat] [--no-html]
```

| 选项 | 说明 |
|---|---|
| `--algorithm` | 只运行指定算法，可重复；顺序按命令行保留 |
| `--episode` | 只运行输入中的第 K 条轨迹（从 0 开始） |
| `--backend` | 选择读取后端，默认 `auto` |
| `--no-html` | 不生成单次 HTML；JSON 与证据仍保留 |

省略 `--algorithm` 时，尝试完整与部分可运行的质量项，并记录不可运行原因。

### `edc regenerate`

完整参数、选择条件与退出码见[按报告筛选并导出数据](../how-to/SELECTION.md)。

### `edc workflow`

```bash
edc workflow check <工作流.yaml>
edc workflow run <工作流.yaml> --output <目录> [--resume]
```

`check` 只校验配置；`run` 执行阶段。`--resume` 从已保存的状态恢复执行。阶段字段见[编排并运行质量工作流](../how-to/WORKFLOWS.md)。

## 环境变量

| 变量 | 作用 |
|---|---|
| `EDC_PLUGINS` | 直接运行的插件文件夹列表，多个用平台路径分隔符（Linux／macOS 为 `:`）隔开；只影响当前命令 |

示例：

```bash
EDC_PLUGINS=examples/plugins/algorithm/joint_jump edc algorithms
```

## 退出码

| 情况 | 退出码 |
|---|---|
| 命令完成；`run` 没有处理错误；`workflow run` 的 `delivery_status` 为 `ready` | 0 |
| `batch` 有轨迹失败；`run` 存在 `processing_error`；`workflow run` 未 `ready`；`robot` 核对不一致或失败 | 1 |
| 未捕获异常、参数错误；`regenerate` 的输出目录或报告不合法 | 2（`regenerate` 的完整规则见[筛选与导出](../how-to/SELECTION.md)） |

退出码 0 表示命令完成，不表示数据质量通过。质量判断以报告的单项结论为准。

## 已知限制

- 没有独立的推荐 CLI，也没有 `edc config export` 命令。
