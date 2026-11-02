# GitHub Python 测试失败定位与修复

失败运行：[41427894142](https://github.com/singlesingerdht/embodied-data-curation/actions/runs/41427894142)，提交 `da7a507`。Python 3.10 和 3.12 均在 pytest 步骤得到 **16 failed / 853 passed / 23 skipped**，安装依赖步骤成功，后续构建步骤因测试失败而未执行。

## 原因与修复

| 范围 | 数量 | 原因与处理 |
|---|---:|---|
| 报告断言 | 13 | 恢复 KUAVO batch 布局后，部分旧测试仍要求参数、推荐、单位、技术明细和数据集级面板，或已替换的文案。测试改查当前报告结论、原始记录、配置及证据目录链接。 |
| 未知共同采集时钟 | 2 | 处理状态仍正确为 `missing_input`，但测试要求原因含英文 `clock`。改查现有“缺少设备时钟映射／未评估”及显式容器时间模式提示。 |
| 机器人配置快捷名 | 1 | CI 使用普通安装，旧路径查找假定可编辑安装，把 `configs` 定位到 Python 安装目录。保留源码配置优先级，并支持从运行所在仓库根目录读取 `configs/robots`。无仓库配置时继续要求显式 `--profile`。 |

没有删除或跳过失败用例；新增 4 个配置查找边界用例。报告模板、算法、阈值、结论标签及真实数据配置均未改动。未完成评估不能建议保留、设备采集时钟未知不能进行采集比较等断言继续保留。

## 本地验证

| 检查 | 结果 |
|---|---|
| 失败用例及 batch 边界定向回归，Python 3.13 | 39 passed |
| 源码全量，Python 3.13 | 873 passed / 23 skipped |
| 安装 wheel 后在源码目录外全量，Python 3.12 | 873 passed / 23 skipped |
| wheel / sdist 构建 | 通过 |
| wheel Python 文件与源码逐字节核对 | 211 个文件一致 |
| 普通安装后在仓库根目录解析机器人快捷名 | `leju_kuavo`、`x1_pro` 均找到配置及参数 |
| 独立基础环境 | 66 项算法（9 CPU / 57 indicators），登记时无可选格式、GPU、模型库导入 |
| 基础 CLI | algorithms / demo fetch synthetic / Structure_Check 通过 |
| 源码目录外的安装包合成工作流 | export / check / run / resume 通过，delivery ready |
| 基础环境及完整格式环境 pip check | 通过 |

测试日志：[定向回归](focused-tests.log)、[源码全量](source-tests.log)、[Python 3.12 wheel 全量](wheel312-tests.log)。跳过项沿用原有可选 LeRobot、研究桥、GPU 扩展及未分发注册证据范围；本轮不新增这些能力的验收。

源码全量命令，在仓库根目录运行：

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/edc -q
```

普通安装验证使用独立的 Python 3.12 venv，安装构建出的 wheel 的 `formats,test` extras 及 `examples/plugins/algorithm`，移除宿主 ROS 的 `PYTHONPATH`，然后在仓库目录外对仓库的 `tests/edc` 运行 pytest。基础 venv 仅安装 wheel 的基础依赖。机器人快捷名分别验证可编辑安装、普通安装＋当前仓库、源码配置优先级及仓库外缺配置四种情况。

GitHub 的 Python 3.10／3.12 矩阵仍执行完整测试、构建、基础安装和合成工作流；可在[分支 Actions](https://github.com/singlesingerdht/embodied-data-curation/actions?query=branch%3Adata-platform)查看推送后的结果。
