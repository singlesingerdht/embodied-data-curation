# 朋友协作验收任务单

目标：由未参与实现的人，在自己的环境中检查安装、数据注册、算法接入、阶段交接和报告是否符合约定。先记录问题，再单独提修复 PR；本轮不修改算法方法、评分公式或阈值。

当前基础及已有结果见 [STATUS](STATUS.md)。以下是**待认领的独立验证任务**，不代表已经由朋友验收。协作代码已推送到 [GitHub 仓库](https://github.com/singlesingerdht/embodied-data-curation)，获得仓库访问权限后可克隆验证。本机工作目录为 `/root/embodied-data-curation`。

## 如何分工

| 编号／优先级 | 建议负责人 | 验证内容 | 通过条件 | 交付材料 |
|---|---|---|---|---|
| A／P0 | 熟悉 Python 环境与打包 | 新机器安装、依赖、源码外运行 | 基础安装无 Torch／模型／私有 wheel；9 个算法可发现；独立 wheel 可导入 | 环境与依赖清单、安装日志、包路径、失败信息 |
| B／P0 | 熟悉机器人数据 | 自行注册数据与逻辑通道、SI 输入 | 推荐与执行绑定一致；左右、反馈／指令不串；已确认单位只换算一次，源值不变 | 配置、源字段与实际绑定对照、原值／SI 值示例 |
| C／P0 | 熟悉采集与时间戳 | 多时间轴及显式 clock 映射 | 三项时间算法实际消费映射；保留原频率；缺映射时不能宣称采集同步 | 原时间／映射后时间、算法输入记录、映射依据 |
| D／P0 | 熟悉后端与测试 | 有限工作流、放行、派生数据与恢复 | 缺输入／失败／待复核不进入价值；编辑和增强按要求复检；修改证据或参数使相应阶段失效 | 阶段轨迹清单、run ID、gate、来源映射、恢复记录 |
| E／P1 | 算法开发者 | 独立插件注册与失败隔离 | 只安装插件即可发现；不改核心分发表；数据集合方法只执行一次；失败有记录且不拖垮其他算法 | 独立插件包、AlgorithmSpec、运行结果及失败用例 |
| F／P1 | 使用者／前端开发者 | 固定报告、图像／视频入口及人工复核 | 能看懂处理状态和数据判断；证据可访问；100 分与待复核可并存；复核不覆盖原评分 | 报告截图、问题帧／片段、复核记录、体验问题清单 |
| G／P1 | 能访问原仓库的人 | CPU 迁移一致性 | 同输入同参数下 9 项逐项一致；只排除已声明的耗时与输出路径差异 | 迁移报告、comparison.json、差异定位 |
| H／P1 | 熟悉 LeRobot 的人 | 官方接口、转换保真与训练侧读取 | 数值／NaN／Inf／彩色图／深度及轨迹边界一致；官方 Dataset 与 DataLoader 可读 | 版本、转换清单、逐项对照、读取日志 |

只有 3 人时，建议分为 **A＋E、B＋C＋G、D＋F**；H 由有官方依赖环境的人单独承担。先覆盖每组的正常与失败场景，再扩大数据规模。

## 通用准备

在新仓库根目录操作，各人使用自己的分支、虚拟环境和全新输出目录。不要复制实现者的虚拟环境，否则无法检验安装是否独立。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[formats,test]'
python -m pip install -e examples/plugins/algorithm
```

示例中 `/tmp/friend-*` 必须换为自己未使用过的目录。安装与测试都要保存 Python／操作系统／依赖版本；测试跳过项单列理由，不计作通过。ROS 环境如注入了旧仓库 `PYTHONPATH`，需清除后再验证安装包。

### A：安装能否脱离开发机

先在开发环境构建：

```bash
python -m build
```

另建干净 venv，仅安装生成的 wheel，再从仓库外操作（以下命令以当前版本为例）：

```bash
python -m venv /tmp/friend-base-env
env -u PYTHONPATH /tmp/friend-base-env/bin/python -m pip install dist/embodied_dqs-0.1.0a3-py3-none-any.whl
cd /tmp
env -u PYTHONPATH /tmp/friend-base-env/bin/python -m pip check
env -u PYTHONPATH /tmp/friend-base-env/bin/python -c 'import dqs; print(dqs.__file__)'
env -u PYTHONPATH /tmp/friend-base-env/bin/dqs algorithms
```

检查导入路径位于新 venv 的 `site-packages`；依赖中没有 Torch、模型权重或私有 wheel。基础包可列出算法，但 HDF5／视频等执行需要额外依赖；缺依赖应明确报出，不能要求基础包执行所有方法。优先补验 Python 3.10、3.12：已有本地结果来自 3.13，CI 配置不等于这些版本已实测。

### B：用户能否正确注册数据

先按 [接入教程](ONBOARDING.md) 和 `examples/robots/` 独立填写一份配置，不沿用实现者的隐藏设置。核对 `inspect → recommend → run` 中的逻辑名、源 topic、字段和实际绑定。

例：左臂关节源值为已确认的 `90 deg`，物理算法应读取约 `1.570796 rad`，源文件仍为 `90`。把单位或语义改为未知后，相关物理算法应缺输入；原值有限性检查仍可执行。请求右臂但源数据只有左臂时必须明确缺输入。

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/dqs/test_logical_channel_names.py tests/dqs/test_unit_normalization.py tests/dqs/test_reading_unit_contract.py tests/dqs/test_explicit_input_bindings.py -q -rs
```

交付至少一份自己的注册配置和一个故意错误的配置，不能只转发已有测试通过截图。

### C：算法是否真的读取多个时间轴

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/dqs/test_workflow_inputs.py tests/dqs/test_config_native_timeline.py -q -rs
```

已有合成用例：关节有 12 个时间点、相机有 3 个时间点；声明 `t_head = 1.1 × t_arm + 0.25 s`，验证 `Check_Timestamp`、`Sync_Check`、`Root_Cause` 的实际输入已换算，同时仍为 12／3 点，原始时间不变。这个映射是测试参数，不是 X1 Pro 标定值。

负例必须包括：只有 `target_clock` 名称、缺少映射、映射超出有效区间、来源为 synthetic。相关跨路采集同步评估不能被放行。真实 MCAP 的映射还要附设备或测量依据；不能凭“两个时刻接近”或“选头部为参考”确认共同时钟。

### D：阶段交接和恢复是否可靠

```bash
python examples/workflows/make_example.py --output /tmp/friend-chain-data
dqs workflow check /tmp/friend-chain-data/workflow.yaml
dqs workflow run /tmp/friend-chain-data/workflow.yaml --output /tmp/friend-chain-results
dqs workflow run /tmp/friend-chain-data/workflow.yaml --output /tmp/friend-chain-results --resume
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/dqs/test_workflows.py -q -rs
```

正常示例六阶段选入数量应为 `2 → 2 → 2 → 2 → 2 → 4`，依次为质量、编辑、复检、盘点、增强、复检；未改变条件时恢复复用六阶段。盘点不是估值方法，亮度变化不是已验证的修复／增强收益。

重点检查：

- 将某项设为缺输入、执行失败或待复核，该轨迹不进入价值阶段；不能只看进程退出码。
- 修改增强参数，只重跑受影响阶段及下游；删除某阶段证据时不能继续复用该阶段。
- 派生数据新 ID、可回读、可定位父轨迹；源文件哈希不变。伪造 identity 映射、越界输出或未声明的数据变化必须失败。
- 当前仅支持逐行 identity 变换。裁剪、删帧、重采样先作为后续契约设计任务，不能据此宣称已经支持。

### E：朋友能否独立开发插件

从 `examples/plugins/algorithm/` 复制为自己的独立包，使用 `team__algorithm` ID 和 `dqs.algorithms` 入口点。可先实现一个简单统计方法，重点验输入输出，不追求复杂算法。插件只从 `dqs` 使用公共接口。

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/dqs/test_plugin_refinements.py tests/dqs/test_algorithm_contract_pool.py tests/dqs/test_workflow_inputs.py -q -rs
```

至少提交：正常运行、缺必需通道、未知单位、缺依赖或故意抛错、重复 ID／不兼容接口的验证。检查推荐与实际执行使用同一通道绑定；单个插件失败有错误记录，其他算法照常运行。价值插件保留自己的原输出，不生成统一价值分。首轮不验 segment 调度。

### F：报告能否帮助使用者定位问题

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/dqs/test_reporting.py tests/dqs/test_video_native_inputs.py -q -rs
```

打开 D 生成的主报告和工作流报告，核对 `catalog.json`、原记录、报告三者一致。选一个问题追到具体轨迹、源通道和帧／时间位置；检查不同机器人使用同一版式。图像入口不应额外编码 MP4；视频入口需标明检查原视频还是派生视频，抽样与缩小比例可见。没有源视频时，容器检查不适用不等于失败。

另按 [复核教程](WORKFLOWS.md#放行与人工复核) 对有线索的具体报告追加一条复核，检查人员、时间、理由、证据及精确 run 引用完整。原评分不变；复核不能把缺输入或未执行项变成通过。报告看不到、证据打不开也应作为体验缺陷反馈。

### G：迁移有没有改变 CPU 方法

需要原仓库的对应快照、格式依赖和 `ffmpeg`；只有新仓库的人不必领取此项。

```bash
python scripts/verify_migration.py --source /root/Datasets_quality_and_value_assessment --output /tmp/friend-migration.json
python scripts/compare_cpu_methods.py --source /root/Datasets_quality_and_value_assessment --output /tmp/friend-cpu-comparison
```

检查迁移清单及 9 项 `equal`，评分、事件、阈值和判断不可忽略。脚本仅规范化 `process_time_ms`、`adapter_elapsed_s` 和输出根路径。它使用合成 ROS2 MCAP；真实数据应在获得授权、固定配置与输入后另做对照，不能把本结果推广成任意真实机器人验收。

### H：LeRobot 是否保留检测证据

Python ≥3.12，单独安装官方依赖；本项不并入基础安装要求：

```bash
python -m pip install -e '.[lerobot,formats,test]'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/dqs/test_lerobot_official.py -q -rs
```

核对固定的官方 LeRobot 版本、官方 `LeRobotDataset` 和 `DataLoader` 可读，数值 dtype／形状／NaN／Inf、彩色像素、深度矩阵、任务、轨迹边界与来源映射正确；索引时间与原始时间分离。逐帧不对应、字段冲突或损坏时不能发布为通过。已有视频副本升级不应默认重新编码。接入真实珞石或灵巧手样例需单列数据版本与结果，不能仅凭合成测试宣称完成。

## 统一反馈格式

每组用一个 Markdown 文件提交，可附 PR 或问题单：

```text
任务编号与验证人：
代码 commit／安装包版本：
系统／Python／关键依赖：
数据来源、是否合成、配置与源指纹：
执行命令与实际 run ID：
预期行为：
实际行为：
通过／失败／跳过及原因：
证据路径（日志、JSON、截图、帧或片段）：
最小复现步骤：
建议修复位置（使用者输入／算法输入输出／阶段交接／展示）：
```

优先交“可复现的问题和证据”，不要以修改算法阈值消除失败。修复 PR 与验收记录分开；公共契约、质量判断、算法方法和许可范围由核心维护者审查。真实价值收益、VR 加速与复杂增强另立实验，不纳入本轮接口验收的完成结论。
