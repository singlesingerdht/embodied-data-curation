# C 动作平滑度 (action_smoothness)

基于机械臂末端位姿的平滑后轨迹 jerk 检测 + SLAM 冻结检测, 与相机无关。

jerk 先在 Savitzky-Golay 平滑后的位置轨迹上计算，再按静止段掩码统计。原始位置与平滑位置的高频残差不参与 jerk，避免把测量噪声按三阶微分放大。静止掩码只保留足够长的低速连续段，动态帧及滤波过渡边界不会进入静止评分。若源时间轴不完全等间隔，先以时间间隔中位数重采样到均匀网格，再求导；原始时间轴和重采样参数写入结果证据。

## 用法

公共工作流使用 `arms: all`，按机器人声明的单臂／双臂分别绑定和执行；具体配置见[统一配置](../../../../../../docs/framework/CONFIGURATION.md)。下述是单文件方法入口。

```bash
python -m edc.algorithms.quality.algorithms.Action_Smoothness.run_check <pose_file> [--side left|right] [--weights '{}']
```

- `--side`: 仅对 `.json` 双臂生效, 选 left/right (默认 right); `.txt` 不需要

## 输入格式

**单位约定 (重要)**: `timestamp` 统一为**秒 (s)**, 位置 `x/y/z` 为**米 (m)** → jerk 单位 `m/s³`。
jerk 阈值 15 即按此标定 (历史数据若为毫秒, 需先 `/1000` 转秒)。

- **`.txt`** — 8 列空格分隔, **第一列为秒**:
  ```
  timestamp(s)  x y z  qx qy qz qw
  ```
- **`.json`** — 含 `fps` 与 `data` 列表, timestamp 由 `帧索引 / fps` 生成 (秒):
  ```json
  {"fps": 20.0, "data": [
    {"follow_left_position": [x,y,z], "follow_left_rotation": [qx,qy,qz],
     "follow_right_position":[x,y,z], "follow_right_rotation":[qx,qy,qz]}
  ]}
  ```
  - 单臂: 用 `follow_position` / `follow_rotation`
  - 双臂: 用 `follow_left_position` / `follow_right_position` (配同名 `_rotation`)

## 检测项 (2 原子)

| 原子 | 阈值 | direction | 说明 |
|---|---|---|---|
| 轨迹抖动 | T_min=15 (熔断), T_max=5 (P80 基线) | inverse | jerk≥15→熔断, ≤5→满分 |
| SLAM 冻结 | ≥1s 位姿不变 且 冻结前有运动(>5mm/s) | binary | 检测到冻结→score=0→熔断 |

- 任一原子 score=0 → 模块熔断 (total=0, DO_NOT_USE)。
- 双臂: 任一臂不通过 → is_bad。
- SLAM 冻结判定用位姿帧间差 (`diff<1e-6`) + 帧数/fps, 与 timestamp 绝对值无关。

## 输出示例

```json
{
  "module": "action_smoothness", "score": 100.0, "grade": "S", "passed": true,
  "verdict": "ACCEPTABLE", "reasons": [],
  "sub_indicators": [
    {"name": "轨迹抖动", "score": 100.0, "passed": true, "raw_value": "Jerk=2.40, P80基线=5.0, 阈值=15.0"},
    {"name": "SLAM 冻结", "score": 100.0, "passed": true, "raw_value": "freezes=0 | 无"}
  ],
  "num_poses": 201,
  "raw": {"jerk": {"score": 2.403, "is_bad": false}, "slam_freeze": {"has_freeze": false, "freeze_count": 0}}
}
```
