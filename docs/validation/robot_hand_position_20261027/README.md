# 乐聚手部源字段读取修复

日期：2026-10-27。适用于 `kuavo_msgs/robotHandPosition` 及其 ROS2 类型名。

## 问题与修复

消息实际包含 `left_hand_position`、`right_hand_position` 两个 `uint8[]` 字段。
此前字段推断从 topic 中的 `position` 生成了不存在的同名字段，解码器又未支持该消息，
使完整 Numeric_Check 报 `missing field; cannot drop or substitute rows`。

现在按源消息类型读取左右字段；保留类型、顺序和时间，缺字段明确失败。
未配置时模态为 `other`，物理量、单位、指令／反馈保持未知。
使用方法与按侧选择见 [手部通道说明](../../framework/ORIENTATION_HAND_TACTILE.md)。

## 实际 bag 核验

使用此前多数据集验证中的同一份乐聚 ROS1 bag、同一份未确认 profile、相同 Numeric_Check 默认参数。
详细原始材料由维护者本地保留，不分发真实数据。

| 检查 | 本次结果 |
|---|---|
| 左、右字段 | 各 666×6，`uint8`；全部元素与直接 rosbags 解码一致 |
| 不存在的 `position` | 明确拒绝读取，不再出现在该通道的字段目录 |
| 完整数值检查 | 从 processing_error 变为 completed；共 11 个字段报告 |
| 有限值 | 9 个非空字段均未发现 NaN／Inf，包含本次左右手字段 |
| 空字段 | 原 `/kuavo_arm_traj` 的 velocity、effort 各 666×0，仍明确未评估 |
| 物理检查 | 单位／语义未确认，仍阻塞，不宣称质量完整通过 |
| 时间与源文件 | 原 header、容器时间、源行顺序和 SHA-256 均保持不变 |
| 算法 | 对照此前验证快照，77 个算法相关 Python 文件 SHA-256 一致 |

本地实际材料：`/mnt/public/dht/dqs_development_results/robot_hand_position_fix_20261027/`，
包含直接解码对照脚本、summary、推荐、catalog、运行记录与 HTML。
此前失败报告保持原状；此次只重跑 Numeric_Check，未重跑其他质量方法。

## 回归与独立安装

- 新增 10 个用例，覆盖类型优先于误导性的 topic、两侧值不同、缺侧、空数组、
  ROS1／DB3／ROS2 MCAP／独立 MCAP、SI 阻塞、数值检查和显式分量选择。
- 本次提交范围的独立快照：[源码回归](publication-source-tests.log)为 **698 passed / 7 skipped**，
  [安装包手部专项](publication-wheel-tests.log)为 **10 passed**；
  [安装验证摘要](publication-package.json)保存构建来源、包指纹和真实 bag 复测。
  此前本地 712 项工作区回归另含 14 项其他工作流测试，本次提交未包含该组改动。
- wheel／sdist 从隔离暂存目录构建；wheel 在仓库外运行回归及同一真实 bag 的数值检查。
  干净基础安装仅依赖 NumPy／PyYAML，9 个内置算法可发现，没有强制安装 ROS、GPU 或模型依赖。

```bash
pip install '.[formats,test]'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/edc/test_robot_hand_position.py -q
```

这是读取适配修复，不改变检测方法、评分或阈值；不代表该机器人的全部自定义消息和物理条件均已验收。
