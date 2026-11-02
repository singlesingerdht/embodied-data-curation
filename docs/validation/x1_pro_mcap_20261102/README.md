# X1 Pro MCAP Quickstart 验收（2026-11-02）

分支 `data-platform`，独立工作区 `/root/embodied-data-curation-data-platform`，基线
`87c5e6f12ccd3d3b1889c6e7594869dd26db3969`。未带入原工作区未提交修改。
原始数据和完整运行材料保留在服务器，不随仓库分发，样例许可未知。

## 复现

在分支根目录按中英文 README 安装 `.[formats]`，依次运行：

```bash
. .venv/bin/activate
edc config check configs/x1_pro.yaml
edc config recommend configs/x1_pro.yaml
edc config run configs/x1_pro.yaml --output results/x1-pro-mcap
edc config export configs/x1_pro.yaml --output results/x1-pro-resolved.yaml
edc batch \
  '/mnt/public/dht/microwave_0124_labled/microwave_0124@MASTER_SLAVE_MODE@2026_01_24_16_12_50/testlhy@MASTER_SLAVE_MODE@2026_04_20_22_19_30_0.mcap' \
  --robot x1_pro --jobs 1 \
  --output results/x1-pro-batch.html --keep results/x1-pro-batch-runs
python docs/validation/x1_pro_mcap_20261102/verify.py
```

`config run` 的预期退出码为 **1**（未满足放行条件）；check/recommend/export/batch/audit 为 **0**。
不要把挂起改成通过来获得退出码 0。新执行的 export 文件及 batch keep 目录须尚不存在。
重复独立 config run 会追加新的运行；审计选择最新一轮。审计只读原始 MCAP，核验九项／双臂绑定、
10 个物理字段、插值权重、跨度、无外推、原始轴、报告和证据路径、batch 参数一致性、SHA256。

## 实测

输入 **663,706,769 bytes**、一条显式 Episode、32 路源通道，任务未知。
运行前后 SHA256 均为：

```text
95993aab130d1ffa2ea9cb9367911f8216f90b2268b076136908f7130053b6d2
```

| 方法 | 处理状态 | 结果边界 |
|---|---|---|
| Structure_Check | completed | 95.5 / ACCEPTABLE，仅结构范围 |
| Numeric_Check | completed | 44 个字段诊断，10 个字段进入物理检测，其余未作物理验收 |
| Action_Smoothness | completed，左右各一条 | 容器时间诊断，均 NEEDS_REVIEW，score=null、assessment_complete=false |
| Check_Timestamp | completed | 单路时间诊断，跨通道采集对齐未评估 |
| Sync_Check | missing_input | 缺共同采集时钟／映射 |
| Root_Cause | missing_input | 缺共同采集时钟／映射，不能归因 |
| Dedup_Check | completed | 缺有效原始时间的通道未完整评估 |
| Reset_Detection | completed | 原 MCAP v2 XYZ 标定，NEEDS_REVIEW |
| Video_Integrity | completed | 四路内嵌 RGB 画面检查，视频容器子项不适用 |

共 **10 条记录、零 processing_error**。工作流 `status=blocked`、`delivery_status=suspended`，
Episode 为 `unassessed`；batch 为“未评估完”，不是整条数据通过。

头部 log_time 重叠帧：左臂 **1,434 帧 / 3.404470426985825 m/s³**，
右臂 **1,435 帧 / 4.113186711416947 m/s³**。这些是保留的原算法诊断值；
容器记录时间不能证明采集同步，也不能据此宣称真实机械振动通过。
图像读到左腕／胸前／头部／右腕 **1,449 / 1,454 / 1,435 / 1,449** 原帧，未转存 MP4。

确认依据来自用户：同型号六轴硬件与 joint1..6 顺序、主臂指令／从臂反馈、位置 rad、速度 rad/s，
及适用标定。主臂速度与本通道位置导数不一致保留为诊断；审计摘要逐关节保存 40ms 窗口的斜率和 RMSE，
该统计不用于反推单位。机械臂 effort 为电流但单位未知，不进行力矩检查。
夹爪／升降米制、头部弧度单位也有登记，但没有用未经确认的阈值做物理验收。

运行结果入口为 `results/x1-pro-mcap/catalog.json`、`report.html`；
`_workflows/<id>/resolved-config.json` 保存工作流／生效配置，`_runs/<id>/run.json` 保存版本和参数。
每轮统一和 batch 各 **35 条 artifact 路径**已核验存在；HTML 内相对链接也存在，
重复运行后的主报告包含历史链接。插值时间、原始纳秒、源行／权重见 Action_Smoothness 的 `timeline.json`。
详细运行 ID、源／安装包路径和数值见 [真实摘要](real-summary.json)、[安装包摘要](wheel-real-summary.json)。

## 测试与安装包

```bash
.venv/bin/pip install -e '.[formats,test]'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests/edc -q
.venv/bin/python -m build
```

源码全量 **846 passed / 23 skipped**；相关时间轴、物理确认、配置、变换及样例专项 **112 passed**。
仓库外安装 wheel 后，同一专项 **112 passed**，且通过导出单文件配置再运行真实 MCAP，
两臂诊断数值及源 SHA256 与源码完全一致。测试集合重叠，不相加。

wheel/sdist 构建成功，wheel **209 个 Python 文件**与源码一致。基础安装使用无 system-site-packages 的
干净 venv 并移除 PYTHONPATH，仅安装本包、NumPy、PyYAML 和 pip；`pip check` 通过，发现 66 个算法，
新增 `edc.time_axis` 可导入，不加载 Torch／OpenCV／MCAP／SciPy／ROS／HDF5。
格式专项环境从 wheel 安装，在仓库外运行，并复用宿主可选格式和测试依赖；这部分并非干净基础环境。
见 [安装验证](package.json)、[基础依赖](base-check.json)、[源码测试](source-tests.log)、
[契约专项](contract-tests.log)、[wheel 专项](wheel-contract-tests.log)。

23 项跳过沿用可选官方 LeRobot、研究桥、GPU 扩展及未分发真实注册证据；
本次不验收设备对时、跨源采集归因、其他采集批次的物理适用性、真实任务识别或许可发布。
旧配置未填 physical_fields 时保持既有检查范围；align_signal 默认沿用选定时间轴。
新显式容器时间消费及字段范围见[配置迁移](../../framework/CONFIGURATION.md)、[变换接口](../../framework/TRANSFORMS.md)。
