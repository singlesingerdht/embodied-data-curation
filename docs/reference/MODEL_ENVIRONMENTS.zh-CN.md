# 模型环境与依赖

[English](MODEL_ENVIRONMENTS.md) · [价值算法](VALUE_ALGORITHMS.zh-CN.md)

EDC 使用统一命令管理可选模型环境。基础安装只负责数据、调度和结果；模型依赖安装在新建的 Python 环境中，通过算法参数 `python` 选择。相容的算法可共用一个环境，OpenPI 的训练依赖和 Transformers 修改版使用独立环境。

OpenPI 环境配置供另行提供的估值实现使用；准备环境本身不会注册估值算法。

## 环境组合

| 配置 | 可选择组件 | 核心版本 | 范围 |
|---|---|---|---|
| `vision` | `progress`、`initial-state`，可组合 | Python 3.12、Torch 2.8.0、TorchVision 0.23.0、Transformers 4.57.1、NumPy 1.26.4 | GRM 组件另固定 vLLM 0.11.0；SAM3 使用本地 wheel |
| `openpi` | `shapley` | Python 3.12、Torch 2.7.1、Transformers 4.53.2、JAX 0.5.3、Flax 0.10.2 | 本地 OpenPI、openpi-client、LeRobot wheel，并应用该 OpenPI 随附的 Transformers 替换文件 |

这些是 Linux x86_64 的安装配置，不代表每个组合都完成了真实模型验收。不能仅凭安装成功宣称算法准确率或完整归因已通过。模型权重不随安装下载。部分原生 SAM3 构建器即使指定 CPU 也会在初始化位置编码时创建 CUDA 张量；导入成功不能证明支持无 GPU 推理，需按所提供源码单独验证。

SAM3 原生依赖要求 NumPy 小于 2；统一视觉配置也约束 OpenCV 4.11，避免安装过程引入要求 NumPy 2 的版本。OpenPI 不与视觉组件混装，其模型代码依赖特定 Transformers 修改版，不能直接升级到统一的 4.57 系列。

## 查看并安装

以下均为**示例路径**。`--python` 指定创建环境的 Python 3.12；`--edc-wheel` 指向与主环境相同的 EDC 安装包。目标目录必须不存在。

```bash
edc env list
edc env plan vision --with progress --with initial-state

edc env create vision --with progress --with initial-state \
  --python /path/to/python3.12 \
  --venv /path/to/envs/vision \
  --edc-wheel /path/to/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source sam3=/path/to/sam3.whl

edc env check --venv /path/to/envs/vision --imports
```

只运行进度归因时，选择 `--with progress`，不需要 SAM3 wheel。用各项目的正常构建方式在独立构建目录准备 wheel；`--source` 接收实际构建产生的完整文件名，不接受可变源码目录或自动替换为另一个上游项目。

OpenPI 的示例：

```bash
edc env create openpi --with shapley \
  --python /path/to/python3.12 \
  --venv /path/to/envs/openpi \
  --edc-wheel /path/to/embodied_data_curation-0.3.0a2-py3-none-any.whl \
  --source openpi=/path/to/openpi.whl \
  --source openpi-client=/path/to/openpi_client.whl \
  --source lerobot=/path/to/lerobot.whl
```

必须使用与估值方法匹配的 OpenPI 源码，LeRobot 对应修订为 `0cf864870cf29f4738d3ade893e6fd13fbd7cdb5`。工具记录提交给它的 wheel 校验值，不替用户证明其上游修订或许可。缺少 OpenPI 所需替换文件、依赖冲突或导入失败都会使安装检查失败。替换仅发生在新建的 OpenPI 环境，保留每个文件替换前后的校验值。

## 通过 EDC 调用

机器人 YAML 中，将解释器放到相应算法的 `checks.<算法 ID>.python`；其他算法参数按[价值算法参考](VALUE_ALGORITHMS.zh-CN.md)提供：

```yaml
checks:
  Initial_State_Distribution:
    python: /path/to/envs/vision/bin/python
    # 同时提供 channel、checkpoint、comparison_space、objects 等参数
  Progress_Stagnation_Attribution:
    python: /path/to/envs/vision/bin/python
    # 同时提供 views、model、task、subtasks 或 segmentation、physical_signals
```

```bash
edc run /path/to/data --robot /path/to/robot.yaml \
  --algorithm Progress_Stagnation_Attribution \
  --output /path/to/new-results --no-html
```

这两项内置价值算法均可通过 `python` 选择独立进程。初始分布未指定 `python` 时继续在当前进程运行；指定后由 EDC 保存原始 RGB 图片与样本映射，子进程复用相同检测与统计代码。主环境仍需安装数据读取和图片处理所需的格式依赖及 Pillow。各进程不会通过环境变量继承外部 Python 模块搜索路径。

## 检查与保留的信息

环境目录的 `edc-environment/` 保存安装配置、输入 wheel 的 SHA-256、pip 安装报告、完整版本快照、`freeze.txt`、安装日志和检查结果。核心版本固定，其余间接依赖由安装时解析并留存；配置文件本身不是所有平台通用的完整依赖锁。

`edc env check` 检查环境隔离、依赖一致性、版本及 EDC 源码是否改变；`--imports` 额外导入所选库，不加载权重或执行推理。成功只表示安装检查通过。更新 EDC 后应从新 wheel 创建新环境并修改 `python` 参数，保留原环境用于回退。已有环境不会被覆盖；失败目录保留日志，修正条件后选择新目录重试。

运行时另外核对主进程和子进程的方法源码，并在模型初始化前保存模型和配置来源。GRM 按检查点声明决定输出层是否共享权重，不修改 Transformers 全局类；已知不兼容的 vLLM 0.11／Transformers 5 组合在依赖检查时报告错误。环境安装不改变评分、时间轴、质量门槛或完整评估条件。
