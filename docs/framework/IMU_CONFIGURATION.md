# IMU 的中间层声明与数据配置

总入口为 [configs/imu.yaml](../../configs/imu.yaml)。这是待绑定模板，未宣称现有 X1 参考 MCAP 已有 IMU。

## 三层绑定

| 层 | 文件 / 契约 | 内容 |
|---|---|---|
| 中间层 | `Channel(modality=imu)`、[机器人模板](../../configs/robots/imu_pending.yaml) | 逻辑名 `imu/main`；`ang_vel: N×3`、`lin_acc: N×3`，可选 `orientation: N×4` |
| 数据侧 | [数据模板](../../configs/datasets/imu_pending.yaml) | `source_bindings` 绑定实际 topic；`capture` 登记本批次字段单位、时间来源、clock、坐标系与校准 |
| 算法侧 | [算法配置](../../configs/algorithms/imu.yaml) | `Numeric_Check.params.channels: [imu/main]`，沿用已有幅值、偏置和冻结检查 |

中间层已有 IMU 数值读取，本次新增标准逻辑名，不重复实现检测器。`imu/main` 不推断安装部位；支持 `imu/base`、`imu/head`、`imu/chest`、`imu/wrist/{left,right,main}` 及 `@实例名`。

## 接入实际数据

1. 在 dataset 配置中填真实 `path`，将 `source_bindings.imu/main: null` 改为清单中查证的 topic 或 feature 名。声明缺失时明确失败；绑到不存在的 topic 时产生 missing_input，不回退到其他通道。
2. 按源数据确认 `field_metadata`。角速度的算法单位为 rad/s，加速度为 m/s²，四元数为无量纲且顺序需登记；源单位分别填写，不能从文件名或逻辑名猜测。当前模板保留 null / pending。
3. 登记本批次 `capture.scope`、`time_source`、`clock`、坐标系和校准状态。ROS2 Imu 示例可选择真实 `header.stamp`，容器 log_time 仍分别保留；不得把两者混用或据此推定跨通道同步。
4. 核验本次合并后的生效 profile 与阈值，再由实际负责人确认 numeric_review。source_bindings 或 capture 改动可能使旧摘要失效；不能只确认未合并的数据模板。运行 `edc config run configs/imu.yaml --output /path/to/new-results`。

该独立 IMU 配置不声明机械臂。配置展开不会自动给它附加 arms；机械臂配置原有拓扑约束及显式 arms 校验不变。接入 X1 时，将 IMU 的 channels 声明合并到 X1 robot，将 source_bindings/capture 合并到对应批次 dataset；不要整份替换已有机器人与数据配置。

ROS `sensor_msgs/msg/Imu` 由已有 reader 提取 angular_velocity→ang_vel、linear_acceleration→lin_acc 和 orientation。不同物理量各自保留字段单位。同一消息内 ang_vel/lin_acc 可供现有联合偏置检测；独立 topic 或 LeRobot 独立 feature 不自动拼接，不以相同行号证明同步。LeRobot 仍须显式 feature_mapping，图像/视频入口不受本次变更影响。

Numeric_Check 对 orientation 当前只执行有限值检查，不宣称姿态误差或协方差已经验收。整条结果仍按既有完成度规则判断，不能把执行 completed 当成全部子项合格。

## 当前真实参考样例

2026-10-27 复核的 X1 MCAP 共 32 个 topic，未发现 `sensor_msgs/msg/Imu`、陀螺仪或加速度源字段。`/odom` 的类型为 `nav_msgs/msg/Odometry`，包含位姿及 twist.linear / twist.angular，没有线加速度字段；采集元数据没有给出 IMU 来源绑定。它不能直接冒充完整 IMU。

因此保留历史 X1 配置和报告，新增 IMU 入口先处于待绑定状态。实测 topic 清单及消息数量见 本轮证据（本地留存：evidence/imu_configuration_20261027/source-topics.json）。

新增测试使用明确标记的合成 ROS2 MCAP，验证源 topic→IMU 中间层→算法→catalog/HTML/CSV，并覆盖待绑定、缺失 topic、字段单位和源时间。合成执行不等于真实设备采集或校准验收。
