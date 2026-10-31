<div align="center">

# 具身数据整理框架

**Embodied Data Curation Framework（EDC）**

统一接入机器人数据，按输入条件选择算法，保留问题定位、处理版本和结果证据。

[English](README.md) · [快速开始](#快速开始) · [配置规范](docs/framework/CONFIGURATION.md) · [算法与输入](docs/framework/ALGORITHMS.md) · [协作验收](FRIEND_VALIDATION.md)

</div>


0.2.0a2：中间层姿态自动统一为 XYZW 单位四元数；支持参数化的 107 项逻辑通道词表及 IMU、里程计、手部、夹爪、触觉模板。源值审计、适用边界和迁移说明见 [姿态契约](docs/framework/ORIENTATION_HAND_TACTILE.md)。

## 最新动态

- **2026-10-31**：X1 Pro 轨迹 JSON＋MP4 格式接入 `edc batch`（`--robot x1pro_episode`），使用厂家 URDF 限位；见 [真实数据批量质检](#真实数据批量质检edc-batch)。
- **2026-10-30**：新增 `edc batch` 批量质检和单页 HTML 报告；乐聚 KUAVO（ROS1 bag，`--robot leju_kuavo`）完成配置与签名。
- **2026-10-26**：公共 Python／CLI 统一使用 `edc`；安装包为 `embodied-data-curation`，源码按六个模块组织。
- **2026-10-26**：统一总入口可引用四类配置并导出完整单文件；机器人与任务数据集独立，支持单臂／双臂范围选择。
- **2026-10-26**：算法推荐与实际执行共享绑定；编辑、增强生成新版本后经过来源校验与质量复检。验证结果与边界见 [STATUS](docs/framework/STATUS.md)。

## 核心能力

- **数据接入**：Dataset → Episode → Channel，保留任务、显式轨迹边界、源字段、selector 和时间来源。
- **算法选择**：展示完整可运行、部分可运行及不可运行的原因；默认执行前两类。部分执行不等于完整达标。
- **明确输入**：逻辑名定位信号；原值保留，已确认物理量进入 SI 执行视图。指令／反馈分别绑定。
- **有限工作流**：默认只做一轮可运行的质量检查，CPU 与 GPU alignment 同属可选质量方法；价值评估按需选择，编辑／增强生成新版本后必须复检。
- **可追溯结果**：独立评分、事件、分布及证据，不产生跨算法总分；缺输入、待复核和执行失败分别记录。
- **算法归属**：当前发布池中的算法全部由本仓库实现和登记，不依赖外部算法包、模型、私有 wheel 或 GPU 运行时。`AlgorithmSpec`／`run(context, params)` 以及 `edc.algorithms`／`edc.readers` 仍保留为后续仓库模块或明确安装的集成模块的开发接口，不会自动改变本仓库的发布算法池。

## 工作流

```mermaid
flowchart TD
    A[数据＋机器人＋算法配置] --> B[输入检查与算法推荐]
    B --> Q[质量算法池：CPU 方法与可选 GPU alignment]
    Q --> G{所选范围是否达标}
    G -->|达标| D{是否选择价值评估}
    D -->|否| O[输出结果、数据引用与证据]
    D -->|是| V[推荐价值方法及各自可用轨迹]
    G -->|需处理且已配置编辑| E[编辑并生成新数据版本]
    E --> C[一致性与来源校验]
    C --> Q2[重新质量检查]
    Q2 --> G
    G -->|缺输入或待复核| P[补充配置或人工复核]
    P --> B
    V --> S[确定各方法的数据子集]
    S --> R[运行价值评估]
    R --> U{是否选择编辑或增强}
    U -->|否| O
    U -->|是| N[编辑或增强并生成新版本]
    N --> C2[一致性与来源校验]
    C2 --> Q3[新版本重新运行所选质量算法]
    Q3 --> G2{是否达标}
    G2 -->|是| O
    G2 -->|否| P2[挂起并说明原因]
```

箭头中的返回通过有限的显式阶段及恢复执行落实，不自动循环修改数据。人工复核不覆盖原分数，不把未执行项改成通过。处理完成与最终放行分别保存在 `status` 和 `delivery_status`。

图中 GPU alignment 表示未来实现位置，当前公共包不依赖外部 GPU 算法或模型。默认运行当前可运行的质量方法；不可运行项保留原因，已执行范围之外不声称通过。价值评估通过显式添加 value 阶段选择。

## 快速开始

最常见的用法：**拿一批机器人数据，检查质量，得到一份网页报告。** 按下面 4 步做。

### 第 1 步：准备环境（只做一次）

需要 Python 3.10 或更新版本。在仓库根目录（`embodied-data-curation/`）运行：

```bash
python -m venv .venv                          # 在仓库里建一个项目专用的 Python 环境，放在 .venv 文件夹
.venv/bin/pip install -e '.[formats,test]'    # 把框架和读取各种数据格式、跑测试要用的库装进去
```

装好后，框架的命令行程序在 `.venv/bin/edc`。服务器副本 `/mnt/public/rxs/code/embodied-data-curation` 已经装好，直接从第 2 步开始。之后所有命令都在仓库根目录运行：

```bash
cd /mnt/public/rxs/code/embodied-data-curation
```

### 第 2 步：确认这台机器人已有配置

每种机器人要有一份配置，告诉框架：哪个字段是关节、单位是什么、限位多少、初始位置在哪。目前已配好、已签名的：

| 机器人 | 数据长什么样 | 命令里写 |
|---|---|---|
| 乐聚 KUAVO | ROS1 的 `.bag` 文件 | `--robot leju_kuavo` |
| X1 Pro | 每条一个文件夹：1 个轨迹 JSON + 3 个 MP4（样例 `input_example/04_episode_json_mp4_x1pro`） | `--robot x1pro_episode` |

其他机器人或格式（如 X1 Pro 的 mcap、ROS2 bag，LeRobot、HDF5）要先新建配置，见 [配置与机器人复用](#配置与机器人复用)。

### 第 3 步：检查数据

```bash
.venv/bin/edc batch 数据文件夹 --robot x1pro_episode --jobs 8 --output reports/我的报告.html
```

- `数据文件夹`：一个或多个文件夹（会往里一层层找数据），也可以是单条数据。
- `--jobs 8`：同时检查 8 条。共享服务器上别开太多。
- 屏幕上每检查完一条显示一行；最后显示各结论的条数和报告位置。

例子：检查 X1 Pro 的 pen_in_cup 任务。

```bash
.venv/bin/edc batch /mnt/public/datasets/x1pro/pen_in_cup --robot x1pro_episode --jobs 8 --output reports/pen_in_cup.html
```

### 第 4 步：看报告

报告是一个网页文件。在服务器上运行时，在**自己电脑**的终端里把它下载下来，再双击打开：

```bash
scp -P 40112 root@113.56.214.18:/mnt/public/rxs/code/embodied-data-curation/reports/pen_in_cup.html 桌面路径
```

报告顶部是统计，下面每条数据一行。每条数据有一个结论：建议保留、建议复核、可救、含建议排除项、未评估完或运行失败；九项检查各一格，点开能看到原因和该去看的时刻。

更多选项、全量运行和重新签名，见下一节 [真实数据批量质检](#真实数据批量质检edc-batch)。

### 进阶：完整工作流演示（用程序生成的假数据）

下面是框架作者给的演示，**用的是程序生成的假数据**，用来展示“检查质量 → 选数据 → 编辑 → 复检”整条流程怎么用配置文件串起来。只做质检时用不到，可以跳过。

```bash
python examples/curation/make_example.py --output /tmp/edc-demo   # 生成一份假数据，以及一份把它串起来的总配置 curation.yaml
edc config check /tmp/edc-demo/curation.yaml                      # 检查总配置写得对不对
edc config recommend /tmp/edc-demo/curation.yaml                  # 列出哪些算法能跑、哪些不能跑，以及原因
edc config export /tmp/edc-demo/curation.yaml --output /tmp/edc-complete.yaml   # 把它引用的几份配置合成一个完整文件
edc config run /tmp/edc-complete.yaml --output /tmp/edc-results  # 按配置跑完整工作流
edc config run /tmp/edc-complete.yaml --output /tmp/edc-results --resume   # 中途停了，从停下的地方接着跑
```

每次运行要用一个新的输出文件夹。结果入口是 `/tmp/edc-results/report.html`（总报告）和 `catalog.json`（结果清单）；每个流程的页面在 `_workflows/<id>/report.html`，每个算法的原始结果在 `_runs/<id>`。

在 Python 代码里也可以做同样的事：

```python
from edc import load_config, recommend_config, run_workflow

configuration = load_config("curation.yaml")             # 读总配置
pool = recommend_config(configuration)                   # 看哪些算法能跑
result = run_workflow(configuration, output="results")   # 跑完整工作流
print(result["delivery_status"], result["outputs"], result["pending"])   # 结论、输出位置、还没完成的项
```

本仓库目前是私有开发版本，公开发布与许可核查见 [发布清单](docs/RELEASE_CHECKLIST.md)。

## 真实数据批量质检（`edc batch`）

一条命令检查一批真实数据：逐条运行九项质量算法，汇总成一份 HTML 报告（同名 `.json` 是数据版，便于统计）。以下命令在仓库根目录运行；`.venv/bin/edc` 是仓库自带环境里的 `edc` 程序，写全路径以免用到别处安装的版本。

已配置并签名的机器人（每个机器人一份配置；限位、速度上限、Home 等都写在各自配置里，互不影响）：

| 机器人 | `--robot` | 数据格式 | 配置 |
|---|---|---|---|
| 乐聚 KUAVO | `leju_kuavo` | ROS1 `.bag` | `configs/robots/leju_kuavo.yaml`，参数 `leju_kuavo/params.json`，签名 `leju_kuavo_signed.yaml` |
| X1 Pro（自变量 Quanta X1 Pro） | `x1pro_episode` | 每条一个目录：轨迹 JSON（`robot-bridge-v260630`）＋ 3 路 MP4 | `configs/robots/x1pro_episode.yaml`，参数 `x1pro_episode/params.json`，签名 `x1pro_episode_signed.yaml` |

```bash
# 文件夹（递归查找 .bag 和单条数据目录；软链接解析后去重）或单个文件，可以给多个
.venv/bin/edc batch /mnt/public/datasets/x1pro/pen_in_cup --robot x1pro_episode --jobs 8 --output reports/pen_in_cup.html

# 按路径清单（每行一条数据）
mapfile -t L < list.txt
.venv/bin/edc batch "${L[@]}" --robot x1pro_episode --jobs 8 --output reports/sample.html
```

| 选项 | 含义 |
|---|---|
| `--robot` | `configs/robots/` 下的机器人名；自动使用 `<名字>_signed.yaml` 和 `<名字>/params.json` |
| `--jobs` | 同时检查几条数据（默认全部同时）；共享服务器建议 8–16 |
| `--output` | 报告文件；旁边另存同名 `.json` |
| `--keep` | 保留每条数据的完整运行结果（`edc run` 输出）到这个新文件夹，用于深挖 |
| `--params` | 换一份算法参数文件（默认用机器人自己的 `params.json`） |

报告里每条数据的结论：建议保留／建议复核／可救（原因清楚能修：时钟固定偏差，或 X1 关节读数卡住、可用末端位姿逆运动学恢复）／含建议排除项／未评估完／运行失败。每一项都写明原因和该看的时刻。

**X1 Pro 全量**（服务器，按任务目录各出一份报告，断了重跑同一命令会接着跑；只含软链接、指向其他目录数据的文件夹自动跳过）：

```bash
tmux new -s x1run        # 离开：Ctrl+B 后按 D；回来：tmux attach -t x1run
bash /mnt/public/rxs/code/edc_tools/x1pro_full_run.sh x1pro_full_v2 16   # 输出到 reports/x1pro_full_v2/
```

脚本副本也在 `input_example/tools/x1pro_full_run.sh`。约 1 万条不重复数据，16 条并行约 1.5 小时。

**X1 Pro 配置要点**：所有数据和画面共用一条行时间（导出时已逐帧对齐），时间戳对齐只查跳帧，采集同步与时间异常归因记为不适用；复位检测用末端位置（容差 5 cm），数据集剪辑记录表明是切片时只查真正的录制开头；关节限位与速度上限来自厂家 URDF（GitHub `X-Square-Robot/sdk_arm_6a` 的 `zbl_arm_6a.urdf.xacro`）；动作平滑度只算原方法找出的真正慢下来的帧；关节读数卡住而末端在动判“可救”。

**测试与重签**：

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/edc/test_x1pro_episode.py   # X1 专项
.venv/bin/python -m pytest -q -p no:cacheprovider tests                             # 全套

# 改了机器人配置里的阈值、通道或标定后，旧签名失效：先把旧签名改名留底，再用自己的名字重签
mv configs/robots/x1pro_episode_signed.yaml configs/robots/x1pro_episode_signed_<日期>.yaml
.venv/bin/edc robot confirm-numeric --profile configs/robots/x1pro_episode.yaml \
    --params configs/robots/x1pro_episode/params.json --reviewer <名字> --output configs/robots/x1pro_episode_signed.yaml
```

## 配置与机器人复用

```yaml
schema_version: edc_config_v1
dataset: {ref: datasets/microwave.yaml}
robot: {ref: robots/x1_pro.yaml}
algorithms: {ref: algorithms/quality.yaml}
workflow: {ref: workflows/curation.yaml}
```

| 配置 | 职责 |
|---|---|
| dataset | 位置、任务、版本、显式轨迹及批次映射 |
| robot | 型号、工程设备代号、部件、逻辑名、物理语义及适用范围 |
| algorithms | 默认执行范围、方法、输入、参数及选择规则 |
| workflow | 串行顺序、放行、子集、编辑／增强和复检 |

同一 X1 Pro 执行开抽屉和微波炉任务时，可只更换 `dataset.ref`。采集批次改变了源字段、单位或时钟时，仍须显式登记相应映射；机器人不会绑定固定目录或任务白名单。

算法默认 `arms: all`：只展开 `parts` 中 `type: arm` 的部件。独立单臂用 `main`，双臂用 `left/right`；单项可设 `arms: [left]`。声明双臂而数据缺一侧会提示部分覆盖，不能自动当成单臂完整通过。

X1 Pro 的 **32 路 topic、23 个逻辑名**合并在 [x1_pro.yaml](configs/robots/x1_pro.yaml)。六路末端 XYZ 的米制语义已登记；关节单位、共同 clock、深度比例和标定中的未知项继续阻塞对应物理检查。[单臂模板](configs/robots/single_arm.yaml)、[完整规范](docs/framework/CONFIGURATION.md)。

## CPU 质量算法

| 算法 ID | 检查内容 | 关键条件与边界 |
|---|---|---|
| Structure_Check | 文件、通道及相机配置一致性 | 按声明的所选范围检查 |
| Numeric_Check | NaN／±Inf 与已配置物理子项 | 原值有限性可运行；物理子项需语义、SI、标定与确认 |
| Action_Smoothness | 末端位置变化和平滑性 | 每条所选臂分别运行，米制反馈及有效记录时间 |
| Check_Timestamp | 各路记录节奏及有条件的跨路对齐 | 相机低频不等于缺帧；跨路另需共同 clock |
| Sync_Check | 独立采集时间轴之间的同步 | 至少两路及共同时钟或验证过的映射 |
| Root_Cause | 时间异常关联和来源归因 | 不能从合成公共行时间推断采集根因 |
| Dedup_Check | 原始时间／序列号重复 | 不判断图像内容，不自动删除记录 |
| Reset_Detection | 开头／结尾的关节反馈（或末端位置）与 Home 的接近情况 | 所选通道、Home、单位和容差明确；可按数据集剪辑记录只查真正的录制开头 |
| Video_Integrity | 原视频或解码画面检查 | `auto/frames/video`，记录抽样及缩放；检查对象不同不混为同一结论 |

详细最低输入、输出和默认绑定见 [算法表](docs/framework/ALGORITHMS.md)。方法、评分公式和阈值保持原有定义；选择范围和适配约束独立记录。

## 价值、编辑与增强

CPU 基础包不强制安装研究或 GPU 环境。默认 `quality_cpu` 预设保留九项 CPU 方法，仓库内的 `quality_indicators` 预设登记按模态展开的指标并按输入条件推荐；未实现或缺输入项明确挂起。真实价值方法逐项接入和验收；数据集盘点、图像亮度变换是 **SDK 接口示例**，自动生产算法池排除这些示例。

价值方法各自推荐可用轨迹，并对自己的子集运行；上游质量结果通过只读 `context.upstream` 提供。需求条件化估值、相对已有数据的互补价值和使用后实际收益属于待验证研究方向，不写成已有工程收益。

编辑／增强返回 `output / params / source_mapping`，保留原始数据，生成可回读的新版本；经过一致性与来源校验后重新检查质量。首轮变换校验仅覆盖 identity 行映射。

## 输出与使用边界

- 格式读取支持 HDF5、Parquet、MCAP、ROS1／ROS2 bag、JSON／episode、MP4 和 LeRobot，具体编码与官方依赖分别验收；可打开文件不代表全部算法适用。
- 未知单位或物理量不进入物理算法。原始时间、容器时间、播放时间、合成时间分别记录；头部相机作为参考通道不自动建立共同 clock。
- 选择轨迹和通道；任意 segment 调度、通用非 identity 变换、无限自动修复、平台账号和真实 VR 加速不在当前支持范围。
- 结果协议沿用 `dqs_record_v1`，其中 DQS 是 Data Quality Service 的缩写；公共接口统一使用 EDC。原评分、证据与历史记录保留。
- 真实数据、模型权重、私有 wheel 不随框架分发；许可未知不表示可公开分发。

## 组织结构与参与开发

```text
embodied-data-curation/
├── README.md / README.zh-CN.md
├── FRIEND_VALIDATION.md
├── pyproject.toml
├── src/edc/
│   ├── data/          # IR、读取、单位与时钟变换、LeRobot 转换
│   ├── robots/        # 机器人配置与部件范围
│   ├── algorithms/    # 注册、契约、推荐与 CPU 方法
│   ├── execution/     # CLI、执行和媒体复用
│   ├── workflows/     # 子集、放行、复核、变换校验与恢复
│   └── reporting/    # 结果与固定版式报告
├── configs/{datasets,robots,algorithms,workflows}/
├── examples/          # 数据接入、插件与完整合成工作流
├── docs/              # 使用规范、状态与证据
├── tests/edc/
└── .github/workflows/
```

当前算法池全部来自本仓库。后续仓库模块或明确安装的集成可使用 `edc.algorithms` 和 `edc.readers`，以 `team__algorithm` 命名并声明 `AlgorithmSpec`、实现 `run(context, params)`；它们不属于基础安装依赖，也不会自动进入生产推荐池。dataset 方法可声明逐轨迹条件并提供集合预检。[扩展模板](examples/plugins/algorithm)、[开发指南](docs/framework/PLUGIN_GUIDE.md)。

三人协作分为数据接入与配置体验、算法插件与输入输出、串行工作流与闭环验收三个模块，负责人、难度和交付物统一见根目录 [FRIEND_VALIDATION.md](FRIEND_VALIDATION.md)。每轮验证统一到同一个 `main` 提交，修改通过分支和 PR 合入；方法接入、许可和公开发布独立验收。
