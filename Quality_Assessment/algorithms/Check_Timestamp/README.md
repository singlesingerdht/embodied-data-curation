# 时间戳对齐检查工具

| 脚本 | 输出 |
| --- | --- | --- |
| `run_check.py` | 正式入口：包级评分、等级划分和统一归因。 |
| `check_timestamps.py` | 辅助诊断：输出 reference -> target 最近邻原始统计。 |
| `check_dt.py` | 辅助诊断：输出单个 topic 的 `dt` 和 CV 统计。 |
| `check_camera_alignment.py` | 辅助诊断：输出相机 frame index、延时和漂移信息。 |

## 环境与依赖配置

建议使用 Python 3.10 及以上版本，并为项目创建独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

`run_check.py`、`check_timestamps.py`、`check_dt.py` 和 `check_camera_alignment.py` 只依赖 Python 标准库。ROS Bag 导出脚本还需要 `numpy` 和 `rosbags`：

```powershell
python -m pip install numpy
```

`export_rosbag1.py` 和 `export_rosbag2.py` 默认查找脚本同目录下的：

```text
rosbags-0.11.3-py3-none-any.whl
```

也可以通过参数显式指定 wheel：

```powershell
python export_rosbag1.py record.bag `
  --rosbags-wheel D:\packages\rosbags-0.11.3-py3-none-any.whl
```

未指定 `--output-root` 时，导出结果写入当前命令行目录；建议在运行前激活 `.venv`。

## `check_timestamps.py`

该脚本只输出匹配事实和原始统计，不产生正式的包级分数；正式评价使用 `run_check.py`。

### 输入

输入目录必须有 `topics.csv`，每个 topic 的 `relative_output` 必须指向对应 topic 的真实时间戳文件。当前评分输入主要使用 `jsonl`。

#### topics.csv格式示例
```csv
topic,msgtype,msgcount,export_kind,relative_output
/cam_h/color/camera_info,sensor_msgs/msg/CameraInfo,127,jsonl,cam_h\color\camera_info.jsonl
/sensors_data_raw,kuavo_msgs/msg/sensorsData,2106,jsonl,sensors_data_raw.jsonl
```
| 表头 | 含义 | 示例 |
| --- | --- | --- |
| `topic` | 原始 ROS topic 名称。 | `/joint_cmd`、`/cam_h/color/image_raw/compressed` |
| `msgtype` | ROS 消息类型，用于说明数据内容的结构，也用于帮助脚本识别相机 topic。 | `sensor_msgs/msg/CompressedImage`、`kuavo_msgs/msg/sensorsData` |
| `msgcount` | 该 topic 在包中的原始消息数量。脚本仅处理 `msgcount > 1` 的流。 | `15000`、`1800` |
| `export_kind` | 导出后的数据文件格式，决定脚本使用哪种读取器。当前用 `jsonl` 。 | `jsonl` |
| `relative_output` | 数据文件相对于输入目录的位置。脚本根据它定位实际的 JSONL 文件。 | `actions/joint_cmd.jsonl` |
时间戳 JSONL 文件每行应至少包含：
```json
{"header_stamp_sec": 1773799451, "header_stamp_nanosec": 768980026}
```

脚本按以下公式得到实际时间戳（单位为秒）：
```text
timestamp = header_stamp_sec + header_stamp_nanosec / 10^9
```
如果源时间戳文件使用浮点字段，例如：
```json
{"timestamp": 1773799451.768980026}
```
评分脚本不会直接读取这种格式。可以先运行 `change_timestamp.py`，它会在原目录下生成 `timestamps/`，将浮点秒拆分为 `header_stamp_sec` 和 `header_stamp_nanosec`，并更新同目录的标准 `topics.csv`。

### 使用

#### 参考流默认为头部rgb相机。--reference可以指定参考
python check_timestamps.py export_package 
  --reference /cam_h/color/image_raw/compressed



#### 导出 JSON / JSONL
python check_timestamps.py export_package --json-out result.json
python check_timestamps.py export_package --jsonl-out results.jsonl


#### 输出

参考流输出 `topic`、`count`；其他 topic 输出数值和基础状态，不输出归因结论：

```text
timestamp valid
count
comparable / source match coverage
nearest median / signed median / P95 / P99 / max
missing match ratio
window IoU
residual jitter / drift span
```

#### 指标与阈值

| 指标 | 含义 |
| --- | --- |
| `timestamp validity` | 单个 topic 自身是否存在空值、负值、回退、重复值或异常大 `dt`。 |
| `comparable` | 与参考流是否具有足够的采集窗口重叠和匹配覆盖。 |
| `nearest median ms` | 参考时间戳到目标流最近时间戳的绝对误差中位数。 |
| `nearest signed median ms` | 最近邻有符号误差中位数；正值表示目标流通常较晚，负值表示通常较早。 |
| `nearest p95 ms` | 最近邻绝对误差的 95 分位数。 |
| `nearest p99 ms` | 最近邻绝对误差的 99 分位数；与 `run_check.py` 的 P99 评分指标一致。 |
| `nearest max ms` | 最近邻绝对误差最大值。 |
| `source match coverage` | 参考帧在匹配窗口内找到目标帧的比例。 |
| `residual jitter ms` | signed offset 去除后的误差 P95。 |
| `drift span ms` | signed error 随帧序号线性拟合得到的漂移跨度。 |
| `missing match ratio` | 最近邻误差超过匹配窗口的参考帧比例。 |
| `window IoU` | 两条流采集时间窗口的交并比。 |

默认匹配窗口：

```text
match_window_ms = max(8 ms, 0.5 × max(reference dt median, target dt median))
```

可比较条件：

```text
window IoU >= 0.95
missing match ratio <= 10%
```

P50/P99、offset、jitter、drift 和 cadence 的具体数值也会作为 `run_check.py` 的 topic 评分输入；该脚本本身只输出事实和数值，不输出归因结论。

`check_timestamps.py` 的历史严格诊断条件，`T` 为目标 topic 的 `dt median`：

```text
nearest P95 <= 0.6 × T
nearest max <= 1.5 × T
```

时间戳自身采样波动的默认提示条件：

```text
dt max > 5.0 × dt median
```

可通过命令行修改主脚本阈值：

```
python check_timestamps.py <输入目录> `
  --dt-gap-factor 5.0 `
  --comparable-window-factor 0.5 `
  --comparable-min-window-ms 8.0 `
  --comparable-missing-ratio 0.10 `
  --comparable-window-iou 0.95 `
  --nearest-p95-period-ratio 0.6 `
  --nearest-max-period-ratio 1.5
```

## `check_camera_alignment.py`

该脚本只用于相机专项诊断，不参与 `run_check.py` 的包级评分。

### 输入

同check_timestamp.py

### 使用

````
python check_camera_alignment.py export_package `
  --reference /cam_h/color/image_raw/compressed
````

未指定 `--reference` 时使用 `topics.csv` 中找到的第一条相机。建议显式指定。

### 输出

```text
终端输出和 `--json-out` 文件均只包含数值指标和基础状态，不输出归因结论。
```
### 指标与阈值

| 指标 | 含义 |
| --- | --- |
| `start offset ms` | 目标第一帧减参考第一帧的时间差。 |
| `end offset ms` | 目标最后一帧减参考最后一帧的时间差。 |
| `offset drift ms` | `end offset - start offset`，描述首尾相对偏移变化。 |
| `frame count diff` | `target count - reference count`。负值表示目标相机少帧。 |
| `index delay median ms` | 同帧序号配对的延时中位数。 |
| `index delay p95 abs dev ms` | 同序号延时相对其中位数的绝对偏差 P95。 |
| `index delay drift ms` | 最后一对与第一对同序号延时之差。 |
| `nearest p95 / p99 ms` | 最近邻绝对误差的 P95/P99；与 `run_check.py` 的 P95/P99 指标对应。 |
| `source match coverage` | 参考帧在匹配窗口内找到目标帧的比例。 |
| `residual jitter ms` | signed offset 去除后的误差 P95。 |
| `drift span ms` | signed error 随帧序号线性拟合得到的漂移跨度。 |

默认阈值定义在 `check_camera_alignment.py` 顶部：

其中 `WINDOW_FACTOR`、`MIN_WINDOW_MS`、`MISSING_RATIO` 与 `run_check.py` 的匹配窗口和缺失匹配失败阈值保持一致；其余阈值仅用于相机专项诊断，不参与包级评分。

```python
WINDOW_FACTOR = 0.5
MIN_WINDOW_MS = 8.0
MISSING_RATIO = 0.10
OFFSET_DRIFT_MS = 3.0
INDEX_P95_ABS_DEV_MS = 2.5
INDEX_DRIFT_MS = 2.0
CLOCK_DRIFT_MS = 5.0
TREND_RESIDUAL_P95_MS = 2.0

```



## `check_dt.py`

该脚本只用于采样稳定性诊断，不参与跨 topic 评分。

### 输入

格式要求与 `check_timestamps.py` 相同。

### 使用

```
python check_dt.py export_package
python check_dt.py export_package --match /joint_cmd
python check_dt.py export_package --sort-by cv
python check_dt.py export_package --json-out dt.json
python check_dt.py export_package --jsonl-out dt.jsonl
```

### 输出

```text
usable count / raw count
zero header skipped / missing header skipped
backward dt count / duplicate dt count
dt mean / median / std / min / max
dt cv / dt cv pct
actual rate hz
JSON 输出中的数值字段与 `run_check.py` 的 `dt_cv`、`long_gap_ratio`、采样周期和采样率对应。
```

该脚本只检查单个 topic 的采样稳定性，不判断跨 topic 对齐。

### 指标说明

| 指标 | 含义 |
| --- | --- |
| `raw count` | 原始文件中的记录数。 |
| `usable count` | 去除缺失或零时间戳后实际参与统计的数量。 |
| `dt count` | `usable count - 1`。 |
| `backward dt count` | `dt < 0` 的数量，表示时间戳回退。 |
| `duplicate dt count` | `dt = 0` 的数量，表示相邻重复时间戳。 |
| `dt mean / median / std` | 相邻时间间隔的均值、中位数和总体标准差。 |
| `dt cv` | `population_std(dt) / mean(dt)`。 |
| `dt cv pct` | CV 的百分数形式。 |
| `actual rate hz` | `1 / mean(dt)`，观测到的平均采样率。 |

`check_dt.py` 没有内置的合格/不合格阈值；它只输出统计量供分析。存在回退或重复时间戳时，CV 和采样率会输出为 `n/a`。

正式评分使用 `run_check.py` 中的阈值：`dt CV` 在 `10%` 以内不扣分，达到 `30%` 时该项归零；`long_gap_ratio` 在 `0.5%` 以内不扣分，达到 `5%` 时该项归零。

## `run_check.py`
### 输入

同check_timestamp.py

### 使用方法
python run_check.py <导出包目录>
python run_check.py <导出包目录> --reference /cam_h/color/image_raw/compressed
### 输出（统一契约）

输出**单对象** JSON，与 A/B/C/projection_alignment 同构，可直接被 `shared/fusion.py` 融合：

| 字段 | 含义 |
| --- | --- |
| `module` / `name` | `check_timestamp` / 时间戳对齐质量 |
| `score` | 包级分数 = 各有效 topic 算术平均（参考流警告时封顶 90） |
| `grade` / `passed` / `verdict` | 沿用 `grade_from_score`：`S>95 / A>90 / B>80 / C>60 / D≤60`；verdict：`>90 ACCEPTABLE` / `60~90 NEEDS_REVIEW` / `≤60 或熔断 DO_NOT_USE` |
| `reasons` | 包级自然语言归因（参考流 topic、平均分、低分 topic 明细） |
| `attribution` | **标准扣分账单**：每个 topic 每个未满分子项一条，含 `sub_indicator/atom/score/gap/weighted_gap`，按 `weighted_gap` 降序，由 `format_report` 从 `sub_indicators` 自动生成 |
| `sub_indicators` | **每个非参考 topic 一项**，含 `score/grade/passed/weight` + `details`（7 子项：匹配覆盖率/P50/P99/系统偏移/残余抖动/漂移/采样稳定性） |
| `threshold_profile` | 参考流、聚合方式、IoU 阈值等审计信息 |

**熔断**（`verdict=DO_NOT_USE`，`score=0`）：存在非法时间戳 / 无可用参考 topic / 参考流 cadence 未过硬门控（`dt_cv≥20%` 或 `long_gap≥5%` 或有回退/重复）。

输出示例（2 个 target，/joint_cmd 有 +5ms 系统偏移）：

```json
{
  "module": "check_timestamp", "name": "时间戳对齐质量",
  "score": 96.8, "grade": "S", "passed": true, "verdict": "ACCEPTABLE",
  "reasons": ["reference topic 为 /cam_h/color/image_raw/compressed。",
              "共评价 2 个 topic，算术平均分为 96.823。"],
  "attribution": [
    {"sub_indicator": "/joint_cmd", "atom": "系统偏移", "score": 74.1, "gap": 25.9, "weighted_gap": 12.9, "direction": "forward"},
    {"sub_indicator": "/joint_cmd", "atom": "残余抖动", "score": 84.5, "gap": 15.5, "weighted_gap": 7.8},
    {"sub_indicator": "/sensors_data_raw", "atom": "系统偏移", "score": 88.2, "gap": 11.8, "weighted_gap": 5.9}
  ],
  "sub_indicators": [
    {"name": "/joint_cmd", "weight": 0.5, "passed": true, "score": 94.3, "grade": "A",
     "gap": 5.7, "weighted_gap": 2.9,
     "details": [
       {"name": "匹配覆盖率", "score": 100.0, "gap": 0.0, "val": 100.0, "t_min": 0.98, "t_max": 0.9},
       {"name": "P50近邻误差", "score": 100.0, "gap": 0.0, "val": 5.01, "t_min": 12.5, "t_max": 33.3},
       {"name": "P99近邻误差", "score": 99.8, "gap": 0.2, "val": 13.2, "t_min": 25.0, "t_max": 66.7},
       {"name": "系统偏移", "score": 74.1, "gap": 25.9, "val": 5.01, "t_min": 3.3, "t_max": 25.0},
       {"name": "残余抖动", "score": 84.5, "gap": 15.5, "val": 2.81, "t_min": 3.3, "t_max": 16.7},
       {"name": "漂移速率", "score": 100.0, "gap": 0.0, "val": 0.05, "t_min": 1.0, "t_max": 5.0},
       {"name": "采样稳定性", "score": 100.0, "gap": 0.0, "val": 3.1, "t_min": 0.10, "t_max": 0.30}
     ]},
    {"name": "/sensors_data_raw", "weight": 0.5, "passed": true, "score": 99.3, "grade": "S", "...": "..."}
  ],
  "threshold_profile": {"reference_topic": "/cam_h/color/image_raw/compressed",
                        "aggregate": "mean(topic_scores)", "iou_threshold": 0.95}
}
```

### 端到端例子

最小输入（一个导出包目录，含 `topics.csv` + 各 topic 的 jsonl）：

```
my_export/
├── topics.csv
├── cam_h_color_image_raw_compressed.jsonl      ← 参考流 (头部 RGB 相机, 默认)
└── joint_cmd.jsonl                              ← 待评估的动作流
```

`topics.csv`（清单：topic 名 / 消息类型 / 帧数 / 格式 / 文件路径）：

```csv
topic,msgtype,msgcount,export_kind,relative_output
/cam_h/color/image_raw/compressed,sensor_msgs/msg/CompressedImage,100,jsonl,cam_h_color_image_raw_compressed.jsonl
/joint_cmd,kuavo_msgs/msg/jointCMD,100,jsonl,joint_cmd.jsonl
```

每个 `.jsonl` 每行一个时间戳（ROS `header.stamp` 拆成秒 + 纳秒两字段）：

```json
{"header_stamp_sec": 1773799451, "header_stamp_nanosec": 768980026}
```

运行 + 拿输出：

```bash
python run_check.py my_export --json-out result.json
```

`result.json` 即上面的统一契约 JSON。`/joint_cmd` 的每个对齐弱项（偏移、抖动等）都会在 `attribution` 里列成"扣分账单"，在对应 `sub_indicators[].details` 里查原始阈值与实测值。

### 评分规则&阈值设置
脚本采用 `reference -> target` 单向最近邻匹配。先检查时间戳合法性和采集窗口 IoU；非法时间戳、`IoU < 0.95`、采样周期不可计算或窗口内无匹配时，该 topic 得 0 分。存在非法时间戳时，包级得分为 0，但仍保留其他 topic 的独立评价结果。

每个有效 topic 由匹配覆盖率、近邻误差、偏移、抖动、漂移和目标流采样稳定性综合评分；包分数为各有效 topic 分数的算术平均。

| 指标/参数 | 含义 |
| --- | --- |
| `iou_threshold = 0.95` | 采集窗口 IoU 低于该值时，topic 得 0 分。 |
| `alignment_scale` | `max(0.5T_reference, 0.5T_target, absolute_error_floor_ms)`。 |
| `match_window` | `max(min_match_window_ms, 0.5*max(T_reference, T_target))`，默认最小窗口为 `8 ms`。 |
| `source_match_coverage` | 参考帧在匹配窗口内找到目标帧的比例；评分区间为 `90%` 到 `98%`。 |
| P50 error | 误差从 `0.75 × scale` 开始扣分，达到 `2 × scale` 时该项为 0。 |
| P99 error | 误差从 `1.5 × scale` 开始扣分，达到 `4 × scale` 时该项为 0。 |
| `signed_offset` | camera 从 `max(10 ms, 0.35T)` 开始扣分，达到 `max(40 ms, 1.2T)` 时为 0；action 对应为 `max(2 ms, 0.1T)` 和 `max(15 ms, 0.75T)`。 |
| `residual_jitter` | 先去除 signed error 的线性漂移趋势，再计算残差绝对值 P95；从 `max(2 ms, 0.1T)` 开始扣分，达到 `max(8 ms, 0.5T)` 时该项为 0。 |
| `drift_rate` | 使用 signed error 的线性拟合速率；拟合至少包含 20 个点、持续 `max(2 s, 10T_reference)` 且 `R² >= 0.5` 时才参与评分。从 `1 ms/min` 开始扣分，达到 `5 ms/min` 时该项为 0。`drift_span` 保留为累计变化诊断值。 |
| target cadence | 由 `dt CV` 和 `long_gap_ratio` 评价；CV 的无扣分/归零阈值为 `10%` / `30%`，长间隔比例为 `0.5%` / `5%`。 |
| 指标权重 | coverage `20%`，P50 `20%`，P99 `25%`，offset `10%`，jitter `15%`，drift `10%`。 |
| alignment / cadence | camera 为 `90% / 10%`，action 为 `70% / 30%`。 |
| 等级 | `S > 95`、`A > 90`、`B > 80`、`C > 60`、`D <= 60`。 |
| `reasons` / `attribution` | `reasons` 是包级自然语言归因（参考流、平均分、低分 topic 明细）；`attribution` 是标准扣分账单（每个 topic 每个未满分子项一条，按 `weighted_gap` 降序），由统一契约 `format_report` 从 `sub_indicators` 自动生成。 |


# ROS Bag 导出工具

## 前置依赖

两个导出脚本依赖 `rosbags`。 可使用 `--rosbags-wheel` 显式指定wheel位置。

```powershell
python export_rosbag1.py record.bag `
  --rosbags-wheel rosbags-0.11.3-py3-none-any.whl
```

## `export_rosbag1.py`

输入为一个 ROS1 `.bag` 文件。导出目录名称为 bag 文件名去除扩展名后的名称，位于 `--output-root` 下。


python export_rosbag1.py record.bag `
  --output-root export


## `export_rosbag2.py`

python export_rosbag2.py ecord.mcap `
  --output-root export

### 导出一个 ROS2 bag 目录。
python export_rosbag2.py D:\data\ros2_bag_dir `
  --output-root D:\data\export

### 递归批量导出父目录下的 ROS2 bag 或 MCAP 文件。
python export_rosbag2.py data\bags `
  --recursive `
  --output-root data\export
```
每个导出包均生成一个以包名命名的目录，至少包括：

```text
<bag_name>/
  topics.csv
  <topic_path>.jsonl
  <camera_topic_path>/timestamps.jsonl
  <camera_topic_path>/000000.<extension>
```

所有 topic 在 `topics.csv` 中使用以下表头：

```csv
topic,msgtype,msgcount,export_kind,relative_output
