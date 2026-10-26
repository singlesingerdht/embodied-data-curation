# 朋友可独立验证的环节

目标是从使用者或插件作者角度验证 EDC（Embodied Data Curation）的流程。不要替换算法公式、评分或默认数值阈值来使测试通过。每个任务可独立认领，通过分支和 PR 交付。

## 统一环境与起步

Python 3.10 或 3.12，Linux，媒体测试安装 ffmpeg。清除宿主 ROS 的 PYTHONPATH，创建自己的 venv。基础安装只允许 NumPy／PyYAML；需要读取格式时安装 formats。

```bash
python -m venv /tmp/friend-edc-env
/tmp/friend-edc-env/bin/pip install '.[formats,test]' ./examples/plugins/algorithm
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/friend-edc-env/bin/python -m pytest tests/edc -q -rs
/tmp/friend-edc-env/bin/python examples/curation/make_example.py --output /tmp/friend-edc-demo
```

以下所有目录换成自己未使用的路径。数据均为明确标记的合成例程；真实数据需另行授权，不能随 PR 上传原始轨迹。

| 可认领任务 | 操作与预期 | 必须提交的证据 |
|---|---|---|
| **P0 独立安装** | `python -m build`；在仓库外全新 venv 安装 wheel；`edc algorithms` 应有九个内置项，不加载 Torch／模型；pip check 成功 | Python／OS／依赖清单、wheel SHA-256、完整日志 |
| **P0 配置体验** | `edc config check/recommend/export`；引用配置与完整单文件应得到相同快照、任务与来源指纹；同一机器人分别用于两个任务 | 两套配置、导出文件、推荐输出、结果差异解释 |
| **P0 单臂／双臂** | `parts.main.type=arm` 默认只检查 main；双臂默认两侧；`arms:[left]` 只取左臂；删除右侧数据不能自动当单臂通过 | 按臂实例、实际 channel_bindings、缺侧原因和 gate；参考 test_unified_curation.py |
| **P0 算法插件** | 根据 examples/plugins/algorithm 新建 `team__id`；声明参数和输入，验证单位未知阻塞物理方法、原值有限性仍可用、插件异常不影响其他算法 | 独立插件包、安装命令、正反用例、预检与执行绑定对照 |
| **P0 串行与方法子集** | 质量 1→2→3，全部所选项达标才进入价值；两个 dataset 方法设置不同逐轨迹条件，分别获得自己的集合；SDK 示例单列 | workflow.json、每方法 eligible/withheld、只读 upstream、run ID 和记录；不写成真实估值收益 |
| **P0 编辑返回复检** | 配置明确失败样本→编辑副本→来源一致性校验→质量复检；无失败时条件编辑跳过，已达标样本保留；缺输入不自动编辑 | 原数据前后 SHA-256、新 ID／版本、source_mapping、复检结果及失败隔离 |
| **P0 增强返回复检** | 价值完成后增强→一致性校验→质量复检；故意使复检缺输入或失败，必须 delivery_status=suspended | 增强输出引用、复检 gate、published=false、pending 原因与原数据哈希 |
| **P1 恢复执行** | 首次 config run 后 --resume；未变阶段复用；改输入／参数／插件版本或删证据后相应阶段不复用 | 两轮 run ID、identity、reused；人工复核后重新 gate 的变化 |
| **P1 报告体验** | 查看 report.html 和工作流页；核对每臂绑定、部分可运行范围、空分数、满分待复核、逐项问题与证据；手机宽度检查 | 脱敏截图、锚点／证据链接检查、与 records.jsonl 的逐项对照 |
| **P1 时间与多格式** | 已有时间单测基础上，验证 acquisition/container/playback/synthetic/unknown；选择头相机不等于有共同 clock；显式映射保留原时间和行对应 | 配置、原始／执行时间证据、缺条件结果；合成时钟不能写成实机同步验收 |

## 完整演示命令

```bash
edc config check /tmp/friend-edc-demo/curation.yaml
edc config recommend /tmp/friend-edc-demo/curation.yaml
edc config export /tmp/friend-edc-demo/curation.yaml --output /tmp/friend-edc-complete.yaml
edc config run /tmp/friend-edc-complete.yaml --output /tmp/friend-edc-results
edc config run /tmp/friend-edc-complete.yaml --output /tmp/friend-edc-results --resume
```

该例程显式选择接口示例，验证质量→编辑→复检→盘点→增强→复检。盘点不是价值算法，亮度变化不证明修复或训练收益。生产自动池排除 `example:true` 的插件。

## 提交要求

PR 写清问题、最终行为、复现步骤、已测范围及边界；附机器可读摘要与精简日志，跳过项列出原因。不得提交令牌、原始数据、私有 wheel、模型权重或未核准许可源码。修改公共契约的任务先补对应边界测试；核心维护者统一审核输入语义、SI、时间、放行和方法行为。

真实价值方法、GPU 模型和平台前端可另外认领，但先完成输入、环境、原始输出、资源与许可验收，不用接口跑通替代真实效果验证。
