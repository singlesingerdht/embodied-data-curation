# 单条与批量报告统一验证

此为本日早先展示方案的历史验证；用户随后要求恢复参考样式，当前行为及复验见 [记录时间与参考报告](../recording_time_20261102/README.md)。

日期：2026-11-02。`data-platform` 工作区中的报告展示变更；本轮未重新计算真实数据的算法结果，也未把未完成项改为通过。

- `edc run`、`edc config run`、`edc batch` 共用 `edc_batch_report_v1` 模板；单条、多条、插件及 dataset 级结果均保留范围。
- 新增七项契约覆盖：单／多轨迹共用模板、显式轨迹边界与缺失算法、dataset 结果隔离、过期 keep 与部分臂覆盖、大记录和不可变证据、历史锚点隔离、空 catalog。
- 源码相关 **101 passed**（报告、batch、工作流、工作流输入、统一配置、MCAP 示例），仓库外安装 wheel 的报告契约 **16 passed**。两组集合重叠，不相加；本轮未重跑全量测试。
- wheel/sdist 构建通过，210 个 Python 文件与源码一致。干净基础 venv 安装与 `pip check` 通过；先验证基础报告不加载格式／GPU／模型依赖，再安装 pytest 执行报告契约。
- 使用安装后的 wheel，在 `/tmp` 下刷新 Quickstart 与 batch 保留结果的 HTML。239 个 catalog、运行、工作流文件哈希前后不变，207 条相对文件链接存在，历史运行轨迹锚点唯一；batch 汇总 JSON 未改。源 MCAP SHA256 仍为 `95993aab130d1ffa2ea9cb9367911f8216f90b2268b076136908f7130053b6d2`。
- Quickstart 页面原为 47,453,528 字节，统一后为 1,935,946 字节（含三次运行）。大型原始记录不嵌入 HTML，按链接访问原 JSONL；评分、状态、证据均保留。整体仍为 `unassessed`。

源码复现：

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/edc/test_reporting.py tests/edc/test_batch.py \
  tests/edc/test_workflows.py tests/edc/test_workflow_inputs.py \
  tests/edc/test_unified_curation.py tests/edc/test_x1_pro_mcap_example.py -q

edc report results/x1-pro-mcap
```

[安装包摘要](package.json)、[安装包报告契约](wheel-report-tests.log)、[真实结果展示核验](real-report.json)。真实原始数据、结果目录和临时安装环境不随仓库发布；本次展示核验不增加采集同步或物理标定的验收范围。
