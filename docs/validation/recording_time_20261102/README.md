# MCAP 记录时间与参考报告格式验证

日期：2026-11-02；工作区 `data-platform`，基线 `leju-kuavo@87c5e6f`。

本页保留同日较早阶段的判定包装结果；当前报告标签及记录时间判定已按用户要求恢复，见[后续验收](../kuavo_report_restore_20261102/README.md)。下方数值与状态为历史记录。

按用户要求，`Sync_Check` 和 `Root_Cause` 可以显式选择容器记录时间继续分析；报告恢复用户提供的 `x1pro_sample50.html` 参考格式。此记录取代本日早先报告统一方案中的额外面板展示。

- 复用现有同步与 gap 关联算法，配置 `timeline: {time_source: container}`。同步参考通道为 `rgb/head`。算法读取真实 MCAP `log_time`，不平移、不生成时间、不修改设备时钟声明。未配置此选项的旧入口保持兼容。
- 原方法分数、判定及子项保留，采集时间结论仍未评估；记录异常进入人工复核，不直接判定采集故障或排除原轨迹。原始容器纳秒、秒制时间及 identity 行映射保存在各算法 `artifacts/<id>/container_timeline.json`。
- 边界测试覆盖：零 acquisition stamp 仍可使用有效 container 时间；采集时间不回退；不同记录 clock 拒绝比较；无效记录轴不排序修复、不能隐藏覆盖缺口；已知合成关联断档被检测；处理完成不能自动形成质量通过。源数据和采集轴保持不变。
- Quickstart 与 batch 实测九项共 10 条记录均为 `completed`。两项记录诊断覆盖 27 路，记录覆盖率 99.8%，相对头部参考的最近邻间隔 P95 最大 49.39 ms。归因有 53 次关联间隔异常，最大记录间隔 0.677 s。分组沿用原方法的命名空间规则，不证明硬件独立；事件也不证明采集丢帧或设备故障。
- 工作流 `status=completed`，交付 `suspended`，轨迹 `unassessed`；CLI 退出码 1 表示未满足交付条件。原 MCAP SHA256 在全部实测前后保持 `95993aab130d1ffa2ea9cb9367911f8216f90b2268b076136908f7130053b6d2`。
- 单条仍为 batch 表格中的一行。主报告 23,815 字节；CSS 与参考附件逐字一致，四个主体标题和四项结论图例一致。新增的配置、流程、技术明细和历史面板已撤回。详情继续使用原版算法表及完整框架输出位置；原始记录、历史与配置保留在 catalog／`_runs`，不嵌入页面。统一、batch 和 batch 保留结果的相对链接均有效。
- 源码检查分批为 29、137、26 passed，集合重叠，不相加。wheel／sdist 构建成功；wheel 的 211 个 Python 文件与当前源码逐字节一致。干净基础 venv 安装与 `pip check` 通过，发现 66 项且不加载格式／GPU／模型依赖；排除了宿主 ROS 的 `PYTHONPATH`。源码目录外 wheel 契约 45 passed，安装包对真实 MCAP 重跑两项记录诊断，结果与 Quickstart 一致。基础 wheel 刷新主报告前后，222 个记录／配置文件哈希不变。本轮未重跑仓库全量测试。

Quickstart 使用 README 中的原命令：

```bash
edc config check configs/x1_pro.yaml
edc config recommend configs/x1_pro.yaml
edc config run configs/x1_pro.yaml --output results/x1-pro-mcap
```

补充 batch 与 README 参数相同；由于已有输出目录，本轮使用新保留目录：

```bash
edc batch \
  '/mnt/public/dht/microwave_0124_labled/microwave_0124@MASTER_SLAVE_MODE@2026_01_24_16_12_50/testlhy@MASTER_SLAVE_MODE@2026_04_20_22_19_30_0.mcap' \
  --robot x1_pro --jobs 1 \
  --output results/x1-pro-batch.html --keep results/x1-pro-batch-recording-runs
```

契约复现：

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/edc/test_recording_time.py tests/edc/test_reporting.py \
  tests/edc/test_config_native_timeline.py tests/edc/test_x1_pro_mcap_example.py -q
```

[真实运行摘要](real-summary.json)、[参考样式核对](layout-summary.json)、[包摘要](package-summary.json)、[安装包契约](wheel-tests.log)、[安装包真实诊断](wheel-real-summary.json)。原始数据及完整运行证据仍在服务器，不随仓库分发，未新增真实样例发布许可声明。
