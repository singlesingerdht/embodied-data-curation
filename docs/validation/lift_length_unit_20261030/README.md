# 直线/升降关节位置单位修复记录（2026-10-30）

代码基线：`d2c63ec` 工作区。未修改原始数据、机器人配置或既有评分阈值。

## 一、问题

`/lift/joint_states.position` 的真实数值恒定在 `0.779984～0.780029`，物理量是升降高度（米），不是关节角度（弧度）。但 `edc robot confirm-numeric` 只接受 `rad`：

```text
$ edc robot confirm-numeric --profile robot_filled_length_m.yaml \
    --reviewer "lift unit validation" --output confirmed.yaml
ValueError: /lift/joint_states.position: explicitly confirm source unit convertible to rad and calibration_status first
```

结果是：要么把升降轴写成不真实的 `rad`，要么数值检查拿不到作者确认，物理检查无法执行。

## 二、根因

`src/edc/robots/numeric_review.py` 把关节位置硬编码成角度：

```python
TARGETS = {('joint_space', 'position'): ('joint_positions', 'rad'),
           ('joint_space', 'joint_q'): ('cmd_positions', 'rad'), ...}
```

同一张映射同时被推荐、作者确认和执行适配使用。后续的单位标签也沿用了 `rad`：

- `numeric_coverage.py`：限位和相邻样本差的 `unit` 固定为 `rad`。
- `numeric_supplements.py`：指令限位和数值冻结的 `unit` 固定为 `rad`。
- `Numeric_Check/numeric_check.py`：事件里的 `threshold_unit` 固定为 `rad per adjacent sample`。

数值检查本身的算法是“相邻样本差/限位”，并不要求输入一定是角度；问题在于单位模型只允许角度。

## 三、修改内容

Numeric_Check 适配器版本在 `leju-kuavo` 分支上由 `ir-adapter-20` 升为 **`ir-adapter-21`**（输入单位契约发生变化；`main` 上对应 `16 → 17`）。

| 文件 | 修改 |
|---|---|
| `src/edc/robots/numeric_review.py` | 新增 `JOINT_POSITION_UNITS=('rad','m')` 和 `joint_position_unit()`；关节位置按声明单位解析 `joint_positions`/`cmd_positions`，`required_unit`、`suggested_unit` 返回实际 SI 单位；`confirm_numeric_profile` 接受可换算成 `rad` 或 `m` 的源单位。 |
| `src/edc/algorithms/quality/adapters/cpu.py` | 把解析出的目标单位传给 `Numeric_Check.assess(..., unit=...)`。 |
| `src/edc/algorithms/quality/algorithms/Numeric_Check/run_check.py` | `assess()` 新增 `unit` 参数并传入关节检测。 |
| `src/edc/algorithms/quality/algorithms/Numeric_Check/numeric_check.py` | `check_joint_anomaly()` 新增 `unit` 参数；事件里的 `threshold_unit` 和结果的 `difference_unit` 使用字段实际 SI 单位。 |
| `src/edc/algorithms/quality/adapters/numeric_coverage.py` | 限位、相邻样本差、指令二阶差分的单位标签取自字段的 `required_unit`。 |
| `src/edc/algorithms/quality/adapters/numeric_supplements.py` | 指令限位、数值冻结的单位标签取自字段 SI 单位；限位说明文字不再固定写 `rad`。 |
| `src/edc/reporting/html.py` | 全局阈值表不再固定写 `rad/样本`；关节类和冻结类阈值改为“字段单位/样本”，由各字段的 `rad`/`m` 决定。 |
| `tests/edc/test_numeric_review.py` | 新增 5 项：`m` 被接受并正确标注、非位置量纲被拒绝、量纲与物理量冲突被拒绝、检测事件单位随字段、报告阈值表使用字段单位。 |
| `tests/edc/test_recommendations.py` | 更新 2 项旧断言：长度关节进入长度目标，不再断言“量纲不匹配”。 |
| `docs/framework/UNITS_AND_ALGORITHM_POOL.md` | 更新“伸缩字段只做有限值检查”的旧说明。 |

## 四、行为边界

- 只放开 `joint_space.position` 和 `joint_space.joint_q`；单位必须能换算成 `rad` 或 `m`。
- `rad`、`m` 以外的量纲仍然阻塞；未知单位、未知物理量仍然阻塞。
- `command`/`feedback`、`calibration_status`、`quantity`、`semantic_status` 仍逐项要求确认；本次没有放宽任何物理语义门槛。
- **阈值数值不变。** 米制字段的相邻样本差/限位阈值按米解释，但默认阈值仍来自角度标定，不自动缩放，也不声称已经是经过米制标定的阈值。
- 既有角度通道的评分、阈值和单位标签保持 `rad`，不受影响。

## 五、验证

### 1. 源码测试

```text
835 passed, 23 skipped（`leju-kuavo` 分支；`main` 为 769）
```

新增的 5 项：

```text
tests/edc/test_numeric_review.py
  test_prismatic_joint_position_accepts_metres_and_labels_the_unit
  test_prismatic_joint_position_rejects_a_non_position_dimension
  test_prismatic_quantity_rejects_an_angular_unit
  test_joint_anomaly_labels_the_declared_si_unit
  test_configuration_panel_uses_field_unit_for_joint_thresholds
```

### 2. 真实配置确认

验证使用一个最小机器人配置（本地留存，不随仓库提交）。把下面内容保存为 `/tmp/robot_lift_m.yaml`：

```yaml
schema_version: 1
id: x1_pro_lift_validation
version: 1.0.0
channels:
  /lift/joint_states:
    modality: joint_space
    fields: [position]
    unit: null
    dimensions: [1]
    time_source: acquisition
    clock: null
    body_part: lift
    side: unknown
    source_kind: feedback
    coordinate_frame: null
    calibration_status: confirmed
    joint_names: null
    field_metadata:
      position:
        quantity: length
        semantic_status: confirmed
        unit: m
        source_kind: feedback
        calibration_status: confirmed
    sensor_id: null
    representation: {}
```

它只声明 `/lift/joint_states`，不修改官方 `configs/robots/x1_pro.yaml`。

```bash
edc robot confirm-numeric \
  --profile /tmp/robot_lift_m.yaml \
  --reviewer "lift unit validation" \
  --output /tmp/robot_lift_m.confirmed.yaml
```

结果：**exit code 0**；升降轴 position 保持 `m`，`numeric_review.status=confirmed`。CLI 只写新文件，不覆盖源配置。

### 3. 真实 MCAP 端到端

把参数保存为 `/tmp/lift_params.json`：

```json
{"Numeric_Check": {"channels": ["/lift/joint_states"]}}
```

用确认后的配置在指定 MCAP 上只跑升降轴这一路：

```bash
DATA='/mnt/public/dht/microwave_0124_labled/microwave_0124@MASTER_SLAVE_MODE@2026_01_24_16_12_50/testlhy@MASTER_SLAVE_MODE@2026_04_20_22_19_30_0.mcap'

edc run "$DATA" \
  --profile /tmp/robot_lift_m.confirmed.yaml \
  --algorithm Numeric_Check \
  --params /tmp/lift_params.json \
  --output /tmp/lift_min_validation
```

结果：

| 项 | 结果 |
|---|---|
| 记录状态 | `completed` |
| 字段报告 | 1 |
| 执行物理检查 | 1 |
| `/lift/joint_states.position` | `physical_checks_applied=True`，`unit=m`，`source_unit=m`，形状 `12069×1` |
| 该字段结论 | `ACCEPTABLE`，`score=100` |
| 相邻样本差单位 | `m per adjacent sample` |
| 数值冻结单位 | `m` |
| 升降限位 | `unconfigured`（本次没有配置升降限位，不是单位问题） |

这里刻意只选升降轴这一路，所以字段报告是 1，不是整机 44。整机 44 字段 / 17 物理检查的结果来自本地完整 profile，只作历史证据，不是本次复现的依赖。

`coverage_complete=False` 是因为升降限位没有配置，不是升降轴单位失败。

验证用的最小 profile、params 和结果 JSON 在本地留存，不随仓库提交。

报告复核：重新生成的报告里，全局阈值表不再写 `rad/样本`，而是 `字段单位/样本`；`/lift/joint_states.position` 一行显示“源 m → 执行 m；关节限位未配置”。

### 4. 安装包验证

- `python -m build --wheel --sdist` 成功。
- 干净 venv 安装 wheel，`pip check` 通过。
- 在仓库外对已安装 wheel 运行新增的 5 项测试：`5 passed`（基于同一补丁在 `main` 上的 wheel；合并到 `leju-kuavo` 后源码全量 **835 passed / 23 skipped**）。
- 安装包内冒烟：长度关节 `physical_checks_applied=True`，`unit=m`，`joint_limits.unit=m`，`joint_step.unit=m per adjacent sample`。

## 六、复现命令

```bash
cd /root/workspace/embodied-data-curation
.venv-b/bin/python -m pytest -q
# 先按第 2 节把最小配置保存为 /tmp/robot_lift_m.yaml
.venv-b/bin/edc robot confirm-numeric \
  --profile /tmp/robot_lift_m.yaml \
  --reviewer "lift unit validation" \
  --output /tmp/robot_lift_m.confirmed.yaml

# 按第 3 节把参数保存为 /tmp/lift_params.json
DATA='/mnt/public/dht/microwave_0124_labled/microwave_0124@MASTER_SLAVE_MODE@2026_01_24_16_12_50/testlhy@MASTER_SLAVE_MODE@2026_04_20_22_19_30_0.mcap'
.venv-b/bin/edc run "$DATA" \
  --profile /tmp/robot_lift_m.confirmed.yaml \
  --algorithm Numeric_Check \
  --params /tmp/lift_params.json \
  --output /tmp/lift_min_validation
```

## 七、没有做的事

- 没有把默认阈值从角度自动换算成米；阈值仍是配置中的数值。
- 没有自动判断某个通道是旋转关节还是直线关节；必须由配置显式声明 `unit` 和 `quantity`。
- 没有修改原始 MCAP、既有 run 结果或历史文档中的数字。
