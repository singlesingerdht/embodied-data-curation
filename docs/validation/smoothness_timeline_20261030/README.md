# 动作平滑度时间轴验收（2026-10-30）

本轮接入 `reference_channel` 与显式 `native`，并按用户要求使用原残差 jerk 方法。正式输出继续将执行状态与质量判断分开。

| 验证 | 实际结果 | 结论范围 |
|---|---|---|
| 正式 MCAP＋原机器人配置＋头部相机参考 | `NEEDS_REVIEW`；无 jerk／质量分 | 两路 acquisition clock 未确认，不插值或假装同步 |
| 同一 MCAP 原生末端时间 | 12,070 帧；jerk RMS 371.792612 m/s³ | 原始约 250 Hz 与 30 Hz 标定不符，数值仅用于诊断；顶层待复核 |
| 独立的共同 clock **假设**对照 | 参考头部真实时间，1,432 帧；3.743689 m/s³ | 仅验证插值和算法消费，不证明硬件或采集同步；不写回正式配置 |
| 均匀 30 Hz 诊断对照 | 4.089634 m/s³ | 合成网格只作数值比较，正式参考模式不生成此网格 |

真实源文件及机器人配置哈希前后不变。完整运行输出在本机 `/root/edc-smoothness-wheel-20261030`，包含 catalog、固定检测报告、逐实例 `timeline.json` 与源行权重。仓库只保存摘要，不分发原数据或假设配置；现场质量结论应使用正式配置。

## 验证范围

- 时间轴专项 23 项：真实抖动参考点、米／毫米 SI、双臂范围、逻辑名绑定、共同 clock、显式仿射映射、未知／不同 clock、映射范围、非递增时间、无重叠、短窗口、NaN、参数冲突及阈值消费。
- 源码与源码外 wheel 全量各 **764 passed / 23 skipped**；跳过项为此前已有的官方 LeRobot、研究桥、GPU 和未分发真实证据条件。本轮不是全部真实数据格式或 GPU 验收。
- wheel 的 206 个 Python 文件与源码一致；基础安装仅本包、NumPy／PyYAML，`pip check` 通过，发现 66 个登记项且不加载 GPU／模型库。完整 wheel 回归复用本机可选依赖；干净基础安装移除了宿主 ROS 的 `PYTHONPATH`。
- 首轮测试暴露的上下文问题已修正：适配器现在保存时间轴证据，集成测试使用正式 `Context`。一次测试期间源码仍在变动，恢复执行检查未通过；冻结源码后专项及最终全量均通过，以这里保存的最终日志为准。

## 复现

用户端参数见 [参考轴配置](../../../configs/algorithms/smoothness_reference.yaml) 和 [配置说明](../../framework/CONFIGURATION.md)。配置不包含共同 clock 的确认；需用户提供经过验证且适用于本批次的声明或映射。`expected_rate_hz` 只检查标定条件，不创建时间。

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/edc/test_smoothness_timeline.py -q
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/edc -q
python scripts/validate_smoothness_timeline.py /path/to/recording.mcap \
  --robot configs/robots/x1_pro.yaml --output /path/to/new-validation
```

只有显式增加 `--experimental-common-clock`，验证脚本才会在独立副本配置中运行假设对照，输出目录必须全新。脚本从不修改输入 MCAP 或机器人文件。

目前只支持已声明 acquisition 参考时间、组件 XYZ 线性插值和共同 clock／直接显式仿射映射。无外推、无自动时钟拟合、无静默窗口缩短、无缺值修复。缺省 native 的频率不符合标定时待复核；更改标定频率声明不能代替真实标定。

详见 [验收摘要](validation.json)、[真实结果摘要](real-summary.json)、[源码日志](source-tests.log)、[wheel 日志](wheel-tests.log)。
