# 统一数据整理工作流

## 两端接口与阶段交接

**使用者端**使用总入口引用数据集、机器人、算法和工作流四类配置，支持导出完整单文件。机器人可供不同任务复用。详见 [统一配置](CONFIGURATION.md)。

**算法开发者端**用 `AlgorithmSpec` 和 `run(context, params)`；算法只读取框架传入的通道及 SI 视图。外部插件的 `channel_param` 与通用 channels 同样解析逻辑名，推荐、预检与执行共用绑定。具体最低输入由 `data_requirements` / `input_requirements` / 自有 preflight 声明。

**阶段交接**携带 `dqs_dataset_ref_v1`，其中有数据集 ID、版本、实际位置、读取后端、配置快照、源文件哈希、episode ID 与 selector。它只是对已有存储的引用，不是新的数据文件格式。`edc.open_dataset_reference()` 校验后可重新打开，包括由不同文件组成的派生集合。

`context.upstream` 是递归只读结构，以阶段 ID 为键，包含精确 run ID、输入／输出数据引用、原始结果和质量放行。价值插件必须读取自身需要的数据；质量结果只是附加上下文。不要把质量评分当成默认价值评分。

## 配置与命令

完整四类配置例程由 `examples/curation/make_example.py` 生成；与 [README 流程图](../../README.zh-CN.md#工作流) 的编辑／增强复检路径一致。

```bash
edc config check curation.yaml
edc config recommend curation.yaml
edc config export curation.yaml --output complete.yaml
edc config run complete.yaml --output /tmp/curation-results
edc config run complete.yaml --output /tmp/curation-results --resume
```

静态 check 不替代输入预检；运行保存完整快照、推荐、按臂绑定、逐方法轨迹子集和证据。退出码只有最终 delivery_status=ready 才为成功；status 单独表示处理完成。stage 协议可供开发者直接构建有限阶段，使用者配置以总入口为准。

## 有限顺序与子集

- 质量 → 价值；编辑形成新版本后必须重新质量检查才能估值。
- 增强必须接在该版本的价值阶段之后；增强输出再做质量复检。默认不重复估值，若需要须显式追加 value。
- 编辑／增强每阶段只能选一个插件且必须写 `select`。通过有限阶段描述编辑与重检，不自动反复尝试修复。
- `select: {all: true}` 或 `select: {episodes: [episode_0]}` 明确选集合；ID 必须存在。
- 可增加 `where`：`[{algorithm: team__method, path: result.reports.0.metric, op: gte, value: 1}]`。只读取**直接输入阶段**的单一已完成结果，不搜索其它历史 run；无法找到唯一字段时不选入。
- value 只接收质量 keep 的子集，再按各方法筛轨迹；augmentation 只接收对该样本适用且选定的价值方法完成后的子集。dataset 方法对自己的集合调用一次，显式 method_select 后重新检查集合最低条件。每方法的输出引用保存在 method_output_references，下阶段数据引用只含成功完成的轨迹并集；未满足条件的轨迹及原因仍保留，没有 segment 调度。

每阶段分别保存可选轨迹、配置选中轨迹、实际进入轨迹以及被拦截轨迹和原因（available/requested/selected/withheld）。阶段执行错误或部分执行会反映在阶段状态；质量判断另看 gate。

同一数据版本的多次质量检查累积放行约束，后一次通过不能抹掉前一次失败／缺输入／待复核。编辑与增强产生新版本，清除父版本的质量资格和价值完成状态。

## 放行与人工复核

放行记录使用 `workflow_gate_v1`，保存在每次 `_workflows/<id>/workflow.json` 的阶段快照中：

| decision | 含义 |
|---|---|
| keep | 所选算法均完整执行，已评估范围达标，待复核已处理 |
| review | 自动结果可满分，但存在需要人工确认的线索 |
| unassessed | 缺输入、缺依赖、执行失败、缺记录、未完整覆盖或只有观察性判断 |
| exclude | 已有不达标判断或人工确认违反所选标准 |

缺记录和未执行项不能靠人工复核补为通过；原 `selection.json` 的历史口径不改写。

```bash
edc review /tmp/curation-results \
  --run-id RUN_ID --algorithm Video_Integrity --object-id EPISODE_ID \
  --report-index 0 --action dismissed --reviewer 张三 \
  --reason '逐帧查看后确认是正常静止场景' --evidence '对应帧与人工检查记录路径'
# confirmed 表示确认该线索违反所选标准；dismissed 表示驳回线索。
edc config run curation.yaml --output /tmp/curation-results --resume
```

`reviews.jsonl` 追加保存人员、时间、理由、证据、run、算法、对象与具体报告哈希。它不覆盖原评分、不自动扣分，也不能覆盖明确的自动失败。复核后恢复执行会复用未变的检测计算，重新计算 gate，再决定是否进入下游。历史工作流的放行快照保留。

## 编辑与增强

返回以下契约，output 为可打开的数据目录或数据引用：

```python
{
    'output': '/results/_runs/RUN/team__edit/artifacts/OBJECT/dataset',
    'params': {'explicit_parameter': 1},
    'source_mapping': {
        'parent_fingerprint': 'exact parent reference fingerprint',
        'episodes': [{
            'output_episode_id': 'new_episode_id',
            'parent_episode_id': 'old_episode_id',
            'rows': 'identity',
            'changed_channels': ['head_rgb'],
        }],
    },
}
```

v1 验证器要求新 ID／版本、插件自身工件目录中的副本、完整 episode 对应关系、相同通道／dtype／形状／时间／语义；未声明变化的数值与图像逐项核对，NaN 位置保留。允许声明具体改变的数值或图像通道，但不证明改变有益。源指纹必须匹配；输出能打开不能代替这些校验。

目前只接受 **identity 行映射**。删帧、裁剪轨迹、重采样、改变维度或时间的真实编辑／增强方法需要新的验证契约，不能把映射伪写成 identity。本轮亮度示例限定 synthetic HDF5，不处理真实采集数据。

不同 episode 的变换失败分别登记；通过校验的副本才能进入下游。原始数据前后重新计算哈希；插件是受信任 Python 代码，哈希可检测源被修改，但本框架没有 OS 沙箱或自动回滚能力。

## 恢复、输出与限制

阶段复用须匹配：数据内容及配置快照、episode 集合／selector、阶段参数、精确上游结果与 run、框架和插件代码、Python／安装包版本、原运行证据。缺失或变更则重跑；历史记录保留。环境指纹采用保守策略，不相关依赖的版本变化也会失效。

`catalog.json` 发现算法 run 和工作流；每次工作流生成独立 `workflow.json` 与 `report.html`，主固定版式报告包含工作流入口。源指纹和派生逐项校验有额外 I/O 成本，当前优先保证可追溯，不宣称零开销。

首轮不提供分布式调度、自动无限编辑、通用人工标注平台或任意非 identity 变换。真实价值、编辑、增强方法须分别验收，不能把盘点示例写成估值实现。

## 条件编辑和最终返回

编辑阶段 `when: needs_editing` 只选明确不达标样本。无此样本则跳过编辑并保留数据引用；有失败样本时保留已达标轨迹，与校验通过的编辑副本共同进入质量复检。缺输入或待复核保持挂起，通过补充配置／复核和 --resume 更新推荐与 gate。

增强后必须配置质量复检；最终 `outputs` 返回数据引用、accepted_episodes、质量状态和 published 标志，`pending` 汇总挂起原因。处理阶段 completed 不保证 delivery_status=ready。增强数据缺输入、待复核或不达标时挂起，不发布成可用数据。
