# 原入口与结果兼容边界

`dqs.open_dataset/run/recommend/report`、单次 `dqs run`、旧 `catalog.json` 与 `_runs` 可继续使用。新增工作流与复核由可选字段、`_workflows` 和独立 reviews.jsonl 表达，不改写旧 selection.json。

`python -m Quality_Assessment.batch_check --input INPUT --output OUTPUT` 继续转发到公共注册执行流程；只支持 workers=1。旧 ndjson 不能用作新执行器断点，必须选择新输出目录。

历史 `assess_trajectory.py` 涉及未迁入的 GPU 视觉与跨模块融合总分，因此留在原研究仓库，不作为新 CPU 包接口。新库使用 dqs run 或 workflow run，不生成跨算法总分。

公共包名保持 embodied-dqs，本轮版本 0.1.0a3；CPU 适配版本 ir-adapter-13。方法文件、原公式和阈值不变；新版本改变统一的物理语义门槛、逻辑绑定和显式时间映射。因此缺语义的旧配置可能新增 missing_input，不能靠更改算法阈值消除。

详见 [迁移基线](../migration/source-manifest.json)、[工作流](WORKFLOWS.md) 与 [STATUS](STATUS.md)。
