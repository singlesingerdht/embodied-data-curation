# Reset Detection Framework

检测机器人在 Episode 结束后是否准确回到 Home Position（复位位置/零位）。

**输入**：机器人的 Episode 数据（关节/末端/底盘/灵巧手等）  
**输出**：每个 Episode 一份 JSON，包含 Initialization 和 Reset 两个 Contract（score + passed + reasons）  
**支持机器人**：yuanli、quanta_x1、kuavo

---

## 1. Installation

**要求**：Python >= 3.10

```bash
pip install -e .
```

核心依赖（自动安装）：numpy, pyyaml, pandas

Kuavo 额外依赖（bag 文件解析）：

```bash
pip install rosbags
```

---

## 2. Quick Start

### Step 1: 编辑配置

```yaml
# configs/default.yaml
robot: kuavo

output:
  dir: /path/to/output

data:
  raw_path: /path/to/episodes

calibration:
  data_path: /path/to/episodes
  exclude_episodes: []
  tolerance1_factor: 2.0
  tolerance2_factor: 3.0
```

### Step 2: 运行校准

从 Episode 数据中自动统计 home_position 和 tolerance，写入 calibrated.yaml：

```bash
python3 -m src.calibration
```

### Step 3: 运行检测

逐 Episode 检测复位状态，输出 JSON Contract：

```bash
python3 main.py
```

### Step 4: 查看结果

```bash
# 终端输出汇总
[Initialization] Total: 10 | PASS: 8 | FAIL: 1 | LOAD_ERROR: 1 | Avg Score: 87.3
[Reset] Total: 10 | PASS: 7 | FAIL: 2 | LOAD_ERROR: 1 | Avg Score: 82.1
Output -> /path/to/output/dataset_detection_output

# 查看具体 Episode 结果
cat /path/to/output/{dataset}_detection_output/{episode}_detection_output.json
```

---

## 3. Configuration

### 3.1 configs/default.yaml

```yaml
robot: kuavo                        # 机器人标识：yuanli | quanta_x1 | kuavo

output:
  dir: /path/to/output              # 检测结果输出目录

data:
  raw_path: /path/to/episodes       # 检测数据路径

calibration:
  data_path: /path/to/episodes      # 校准数据路径
  exclude_episodes: []              # 排除的 Episode 名称列表
  tolerance1_factor: 2.0            # 紧阈值 sigma 倍数（默认 2.0）
  tolerance2_factor: 3.0            # 宽阈值 sigma 倍数（默认 3.0）
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `robot` | 机器人标识 | `yuanli` |
| `output.dir` | 检测结果输出目录 | `detection_summary` |
| `data.raw_path` | 检测数据根目录 | — |
| `calibration.data_path` | 校准数据根目录 | — |
| `calibration.exclude_episodes` | 校准时排除的 Episode | `[]` |
| `calibration.tolerance1_factor` | 紧阈值 sigma 倍数 | `2.0` |
| `calibration.tolerance2_factor` | 宽阈值 sigma 倍数 | `3.0` |

### 3.2 calibrated.yaml

校准后自动写入 `configs/robots/{robot}/calibrated.yaml`，包含三部分：

```yaml
robot:
  name: kuavo
  parameters:
    arm:
      left_arm_joint_positions:
        type: vector
        calibrate: true
        detect: true
      gripper_joint_positions:
        type: vector
        calibrate: true
        detect: false
    base:
      base_orientation:
        type: quaternion
        calibrate: true
        detect: true
    # ...

home_position:       # 复位参考位姿（校准统计均值）
  arm:
    left_arm_joint_positions: [...]
    # ...

tolerance1:          # 紧阈值（100 分边界）
  arm:
    left_arm_joint_positions: [...]
    # ...

tolerance2:          # 宽阈值（0 分边界）
  arm:
    left_arm_joint_positions: [...]
    # ...
```
如果有特定零位也可手动设定数值。

**参数属性**：

| 属性 | 说明 |
|------|------|
| `type` | 参数类型：scalar, vector, quaternion |
| `calibrate` | 是否参与校准统计（计算 home/tolerance） |
| `detect` | 是否参与评分判定（影响 score 和 passed） |

---

## 4. Calibration

从多个 Episode 的首帧数据中统计计算 home_position 和 tolerance。

**输入**：Episode 数据目录 + calibrated.yaml 中的参数定义  
**输出**：更新 `configs/robots/{robot}/calibrated.yaml`

```
Episode 首帧数据
      │
      ▼
统计量计算（mean, std, min, max, count）
      │
      ├──→ home_position = mean
      ├──→ tolerance1 = tolerance1_factor × std
      └──→ tolerance2 = tolerance2_factor × std
```

### CLI

```bash
# 使用默认配置
python3 -m src.calibration

# 指定配置和数据路径
python3 -m src.calibration \
  --config configs/default.yaml \
  --data-path /path/to/episodes

# 指定容差因子
python3 -m src.calibration \
  --tolerance1-factor 2.0 \
  --tolerance2-factor 3.0

# 干运行（仅打印，不写入文件）
python3 -m src.calibration --dry-run

# 覆盖机器人
python3 -m src.calibration --robot kuavo
```

| 参数 | 说明 |
|------|------|
| `--config, -c` | 配置文件路径 | 
| `--data-path, -d` | 覆盖校准数据路径 |
| `--robot, -r` | 覆盖机器人标识 |
| `--tolerance1-factor` | 覆盖紧阈值 sigma 倍数 |
| `--tolerance2-factor` | 覆盖宽阈值 sigma 倍数 |
| `--dry-run` | 仅打印结果，不写入配置文件 |

---

## 5. Detection

对每个 Episode 产出两次独立评估：

- **Initialization**：首帧检测 — 机器人开始时是否在 Home Position
- **Reset**：末帧检测 — 机器人结束后是否回到 Home Position

两次评估使用相同的 home_position 和 tolerance，但分别基于首帧和末帧数据。

### CLI

```bash
# 使用默认配置
python3 main.py

# 指定数据路径和输出目录
python3 main.py \
  --data-path /path/to/episodes \
  --config configs/default.yaml \
  --output-dir /path/to/output

# 覆盖机器人
python3 main.py --robot quanta_x1
```

| 参数 | 说明 |
|------|------|
| `--config, -c` | 配置文件路径 |
| `--data-path, -d` | 覆盖检测数据路径 |
| `--output-dir, -o` | 覆盖输出目录 |
| `--robot, -r` | 覆盖机器人标识 |

---

## 6. Score V2

### Parameter Score

每个 detect=true 的参数，基于双阈值线性插值计算 0-100 连续分数：

```
error ≤ tolerance1
        ↓
     100 分

tolerance1 < error < tolerance2
        ↓
     线性下降

error ≥ tolerance2
        ↓
       0 分
```

公式：

```
score = 100 × (tolerance2 - error) / (tolerance2 - tolerance1)
```

### Assessment Score

一个 Episode 的 Assessment Score 由所有 detect=true 参数的 Parameter Score 聚合：

- **任意 Parameter Score = 0** → Assessment Score = 0，passed = false
- **否则** → Assessment Score = 所有 Parameter Score 的平均值

### Passed

`passed = (Assessment Score > 0)`

### Reasons

未满分的参数会生成原因描述：

```json
[
  "左臂末端位置未完全回到初始状态（72分）",
  "机器人基座姿态严重偏离初始状态（0分）"
]
```

---

## 7. Output

### 目录结构

```
{output.dir}/
└── {dataset}_detection_output/
    ├── {episode}_detection_output.json
    ├── {episode}_detection_output.json
    └── ...
```

其中 `dataset` 为数据根目录的文件夹名。

### JSON 示例

每个 Episode 产出一份**单对象** JSON（统一契约，与 A/B/C/yjy/projection_alignment 同构，可被 `shared/fusion.py` 融合）。`Initialization` 与 `Reset` 作为两个独立平级的 `sub_indicators`，模块分取 `min`：

```json
{
  "module": "reset_detection",
  "name": "复位检测",
  "score": 72.0,
  "grade": "B",
  "passed": true,
  "verdict": "NEEDS_REVIEW",
  "reasons": [
    "[Reset] 左臂末端位置未完全回到初始状态（72分）"
  ],
  "attribution": [
    {"sub_indicator": "Reset", "atom": "左臂末端位置", "score": 72.0, "gap": 28.0,
     "weighted_gap": 14.0, "value": 0.05, "t_min": 0.02, "t_max": 0.05}
  ],
  "sub_indicators": [
    {"name": "Initialization", "weight": 0.5, "passed": true, "score": 100.0, "grade": "S",
     "gap": 0.0, "weighted_gap": 0.0,
     "details": [
       {"name": "左臂末端位置", "score": 100, "gap": 0, "val": 0.0, "t_min": 0.02, "t_max": 0.05},
       {"name": "右臂关节", "score": 100, "gap": 0}
     ]},
    {"name": "Reset", "weight": 0.5, "passed": true, "score": 72.0, "grade": "B",
     "gap": 28.0, "weighted_gap": 14.0,
     "details": [
       {"name": "左臂末端位置", "score": 72, "gap": 28.0, "val": 0.05, "t_min": 0.02, "t_max": 0.05},
       {"name": "右臂关节", "score": 100, "gap": 0}
     ]}
  ],
  "warnings": [],
  "threshold_profile": {
    "tolerance1_factor": "2σ",
    "tolerance2_factor": "3σ",
    "aggregate": "min(Init,Reset), any-melt→DO_NOT_USE"
  },
  "episode_path": "/data/ep1"
}
```

### 聚合语义

- **Init 与 Reset 独立平级**：各自 score 完全由自己那帧的参数算，互不影响（不分主辅）。
- **模块分 = `min(Init, Reset)`**：两者都得对，取最差的一个作为整条数据的复位质量（不让好的那个拉高差的）。
- **熔断**：任一参数 score=0 → 该评估 `passed=False` → 模块 `passed=False` → `verdict=DO_NOT_USE`、`score=0`。
- **`grade`** 用 `to_grade`（≥95 S / ≥85 A / ≥70 B / ≥50 C / <50 D）；**`verdict`**：`passed=False → DO_NOT_USE` / `score<85 → NEEDS_REVIEW` / 否则 `ACCEPTABLE`。

### 字段说明

| 字段 | 说明 |
|------|------|
| `module` / `name` | `reset_detection` / 复位检测 |
| `score` | 模块分 = `min(Initialization, Reset)`，任一熔断 → 0 |
| `grade` / `passed` / `verdict` | 等级 / 是否通过 / 三态判定（见上"聚合语义"） |
| `reasons` | 未满分参数的自然语言归因，带 `[Initialization]`/`[Reset]` 前缀 |
| `attribution` | **扣分账单**：每个 `gap>0`（未满分）的参数一条，含 `sub_indicator/atom/score/gap/weighted_gap/value/t_min/t_max`，按 `weighted_gap` 降序，由 `format_report` 从 `sub_indicators` 自动生成 |
| `sub_indicators` | `Initialization` 与 `Reset` 两个独立子指标，各含 `score/grade/passed/details`；`details` 是各参数 atom（`name/score/gap/val/t_min/t_max`） |
| `threshold_profile` | 聚合方式、容差因子（`tolerance1=2σ`/`tolerance2=3σ`）等审计信息 |
| `episode_path` | 该 Episode 的数据路径 |

---

# Part 2: Architecture

## 8. Core Concepts

| 概念 | 说明 |
|------|------|
| **Episode** | 一次完整的任务执行，包含时间序列采样的关节/末端数据帧 |
| **Home Position** | 复位参考位姿，由校准从数据中统计生成 |
| **Initialization** | 首帧检测 — 机器人开始时是否在 Home Position |
| **Reset** | 末帧检测 — 机器人结束后是否回到 Home Position |

---

## 9. Supported Robots

| 机器人 | 数据格式 | Groups |
|--------|---------|--------|
| **yuanli** | `episode_dir/meta.json` + `actions/*.jsonl` | `left_arm`, `right_arm` |
| **quanta_x1** | `episode_dir/*.json` | `left_arm`, `right_arm`, `base`, `lifting`, `head` |
| **kuavo** | `*.bag`（ROS1 bag） | `arm`, `leg`, `base`, `imu`, `dexhand` |

---

## 10. Extending a New Robot

1. **实现 Loader**：继承 `BaseLoader`，实现 `load_episode()` 返回 `Episode` 对象

2. **注册到 Factory**：在 `LoaderFactory._registry` 中注册

3. **创建配置**：在 `configs/robots/{name}/calibrated.yaml` 中定义参数 Schema

4. **运行校准**：`python -m src.calibration --robot {name}` 自动生成 home_position + tolerance1 + tolerance2
