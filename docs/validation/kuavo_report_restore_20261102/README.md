# KUAVO 报告输出恢复验证

日期：2026-11-02；工作区 `data-platform`，基线 `leju-kuavo@87c5e6f`。

按用户要求，报告使用原有通过／复核／可救／排除及不适用，不增加结论标签；单条仍为 batch 表格的一行。没有质量判定和缺输入的项目显示不适用并保留原因。内部原始状态和历史记录保留，运行错误不伪装通过，缺失评估不能形成整条建议保留。

显式选择容器时间的动作平滑度、同步及归因，恢复原方法的评分和判定，撤回仅因容器时间而进行的结论覆盖。容器时间结论只描述该输入范围，设备采集时钟映射仍未知。时间轴、原始纳秒、源行、插值权重、禁止外推和最大 0.1 秒插值跨度均保留；算法阈值不变。

真实 MCAP 按 README 的 check → recommend → run 重新执行，退出码依次为 0／0／1；最后的 1 表示未满足交付条件。主运行 `20261102T094526Z-c65bb48102` 有九项、双臂共 10 条 completed 记录及 39 个证据文件。双臂平滑度各 100 分／通过，同步 99.9 分／通过；归因原方法为 0 分／排除，整体显示含建议排除项。时间戳对齐不适用，数值及复位复核。同步及归因输入仍为同一 MCAP 的 27 路记录时间，99.8% 覆盖、最大最近邻 P95 49.39 ms、53 次关联间隔异常；这些指标不证明设备采集故障。

补充 batch 也重新执行，因旧保留目录已存在，使用 `--keep results/x1-pro-batch-kuavo-runs`，其余参数与 README 相同。主报告、batch 及三个保留输出共五处 HTML，CSS 与参考附件逐字一致，四个主体标题、四项图例一致，链接全部有效，均无参考／未评估完结论标签。原 MCAP SHA256 前后均为 `95993aab130d1ffa2ea9cb9367911f8216f90b2268b076136908f7130053b6d2`；328 个既有运行／配置文件哈希未变。

源码专项 **91 passed**；源码目录外安装 wheel 专项 **90 passed / 1 deselected**。安装包首轮含源码布局测试时为 90 passed／1 failed，失败原因是 `test_inputs_and_robot` 依赖 editable checkout 的 `configs/robots`，该测试已在源码通过；安装包可移植契约单独排除该项，不声称 wheel 支持源码布局的 `--robot` 快捷名。wheel／sdist 构建、干净基础安装和 `pip check` 通过；211 个 Python 文件与源码一致，基础安装发现 66 项且未加载格式、模型或 GPU 库，并成功刷新真实主报告。本轮没有执行仓库全量测试。

源码契约命令：

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/edc/test_smoothness_timeline.py tests/edc/test_recording_time.py \
  tests/edc/test_reporting.py tests/edc/test_batch.py \
  tests/edc/test_config_native_timeline.py tests/edc/test_x1_pro_mcap_example.py -q
```

[真实运行摘要](real-summary.json)、[样式与链接](layout-summary.json)、[包摘要](package-summary.json)、[基础安装](base-summary.json)、[安装包专项](wheel-portable-tests.log)、[安装包首轮结果](wheel-tests.log)。完整报告与真实数据留在服务器，不随仓库分发；本轮未新增数据发布许可或设备对时能力。
