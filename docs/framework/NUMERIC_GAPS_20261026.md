# Dayan 数值检查缺口补齐：A5 / I7 / J7 / A7

本轮在现有 `Numeric_Check` 中补两类检查，不新增重复算法 ID。实现为仓库自有逻辑，未复制私有 Dayan 源码。对照依据仍是此前审计的 Dayan 提交 `a81267d291d2be033da257ab830625be7c136a74`；这不是一次远端最新版本审计，也不表示 Dayan 全部指标均已迁入。

| Dayan 对照项 | 本轮增加 | 判断边界 |
|---|---|---|
| A5 动作指令限位 | 按显式 command 字段、SI 弧度、逐分量闭区间检查 | 独立配置；不借用反馈 joint.limits；只支持已声明的角位置指令 |
| J7 / A7 数值冻结 | 反馈位置和指令位置分别检测恒值区间 | 运动后恒值为复核候选，普通恒值仅观察；不证明采集故障 |
| I7 IMU 数值冻结 | ang_vel 和 lin_acc 各自逐分量检测 | 需要 N×3；同一 Episode 内检测，不作跨通道配对 |

## 例子

假设仅作合成测试的单关节指令限位为 `[-1, 1] rad`：

- `[-1, 0, 1]` 包含边界，没有越界；其他适用检查也完整时可保留。
- `[0, 0.1, 1.01]` 的第 2 行越上限；历史评分可能仍是 100，但新增指令限位明确失败，筛选为 `exclude`。
- `[0, 0, 0, 0]` 是恒值观察；仅凭这些值无法证明采集冻结，不因此排除或请求复核。
- `[0, 0.1, 0.2, 0.2, 0.2]` 的 `[2, 5)` 是运动后恒值候选；它也可能是正常动作保持，因此先 `review`。人工复核可以确认问题或排除误报。
- 未填独立限位、维度不匹配、样本不足、未确认单位或配置时，整体为 `unassessed`。已观测到的明确越界／非有限值仍保留排除结论。

这些都是合成例子，不是任何真实机器人规格或倒茶示教验收。

## 配置与执行

沿用 `robot.yaml` 的 `numeric_check.thresholds`。下面限位仅供单维合成数据使用；真实运行须依据真实机器人与指令契约填写。

```yaml
numeric_check:
  thresholds:
    command_limits:
      limits: [[-1.0, 1.0]] # rad，按当前选择字段的分量顺序逐项填写
    cmd_freeze:
      min_run: 3
      lookback: 10
      freeze_eps: 0.000001
      motion_threshold: 0.01
```

另外三组为 `joint_freeze`、`gyro_freeze`、`accel_freeze`，具有相同参数名与默认建议数值。关节和指令阈值单位为 rad，角速度为 rad/s，加速度为 m/s²。建议值必须结合采样、传感器精度和任务正常保持行为审核；不能将默认值理解成实际硬件规格。既有 `pose_freeze` 的方法和阈值含义保持原样。

`min_run` 为最少样本数，`freeze_eps` 约束整个候选区间的最大值减最小值，避免把每步很小但累计变化的慢运动判为整段冻结。扫描保留满足全幅差阈值的最长窗口，近恒值窗口可能重叠，不应将其样本数相加作为独立冻结样本量。这是候选检测，不保证召回所有冻结故障。`lookback` 控制回看样本，回看区间含当前候选起点；其全幅差严格大于 `motion_threshold` 才标运动后恒值。`motion_threshold` 必须大于 `freeze_eps`。所有区间限于当前 Episode，末尾未恢复运动的区间也会记录。

`command_limits.limits` 默认 `null`，表示尚未配置，不能宣称已完成 A5。长度必须等于本次所选字段维数；包括通过 `select.indices` 得到的虚拟通道，顺序也是选后顺序。一个运行配置中的限位列表适用于本次选择的所有指令字段；不同机械臂需要不同限位时，应通过 `channels` 限定范围并分别配置运行，不猜测或重排限位。

源字段仍须明确 `source_kind: command/feedback`、物理量、单位和校准；状态与动作不互换。限位和冻结阈值始终是 SI；例如源值是 deg，由既有 SI 执行视图换算为 rad，源文件不变。

编辑真实配置后沿用现有显式确认入口：

```bash
edc robot confirm-numeric --profile robot.yaml --reviewer "实际确认人" --output robot.confirmed.yaml
edc run /path/to/data --profile robot.confirmed.yaml --algorithm Numeric_Check --output /path/to/new-results
```

本轮未替机主确认或修改任何真实机器人限位。新增阈值也纳入配置摘要，升级前的确认记录会变成 `stale`；需要作者审阅新增项后重新确认，不能复用旧摘要绕过。

## 时间、证据和筛选

恒值检测按源行执行，不依赖帧率。时间跨度只取本通道已有的 acquisition 或 container 秒制时间，且要求长度一致、有限、严格递增。持续时间为区间首样本到末样本之差；container 仍标为 container，不声称是采集持续时间。unknown、synthetic、playback、重复或回退时间均只显示行区间，不生成替代时间轴。

结果保存原文件、Episode ID、selector、通道来源映射和选前分量索引。LeRobot 的文件/行映射保留在 `channel_source_mapping`，这里的源行是 Episode 内从 0 开始的行。没有跨通道匹配或同步结论，也没有改动 LeRobot 的 image / video 两入口。

`supplemental_checks` 与 `check_coverage.checks` 保存新子项；全部越界事件、恒值观察与候选区间另外写入 CSV，由现有 `catalog.json → _runs/<run_id>` 发现。HTML 展示分项状态、源分量、行区间、时间跨度和原因。

- 原来的 `score`、`passed`、`verdict`、`numeric` 和子项评分保持原判据。
- 新限位失败通过 `supplemental_verdict: DO_NOT_USE` 参与统一筛选，不通过修改历史评分表达。
- 冻结候选写入 `review_candidates`；普通恒值写入 `observations`。缺少其他适用检查时仍不能以复核代替完整评估。
- `assessment_complete` 保留既有检测器的资格范围；新子项实际完成度在 `coverage_complete`，直接运行与工作流均消费该字段。
- 参数无效明确失败；字段形状/样本数不合契约、限位维数不符分别保留未完成状态，不把它们解释为坏数据。

Numeric 适配器版本为 `ir-adapter-16`，筛选策略为 `episode_filter_v3` / `workflow_gate_v3`。本轮补项在注册算法的 `run(context, params)` 中执行；直接调用旧 `Numeric_Check.run_check.assess()` 仍只执行原检测器，不能将其当作新补项验收入口。历史文件不改写；恢复执行通过既有算法和代码指纹重新核验。

## 剩余边界

本轮没有覆盖跨独立 feature 的 IMU 配对、字段/模式一致性、结构完整性扩展、依赖双时钟的检测，以及 Dayan 上游未实现项。C6 原编码突变不在本轮；恒值检测也不代替已有时间戳重复检测。发布许可及真实样例验收沿用各自清单，不因合成测试通过标为完成。

验证日志与安装包信息见 本轮证据（本地留存：evidence/numeric_gaps_20261026/validation.json），当前验收汇总见 [STATUS](STATUS.md)。
