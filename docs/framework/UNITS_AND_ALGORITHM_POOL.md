# 单位未确认时的接入与算法推荐

源文件只读。算法方法、评分公式和默认阈值不因本次适配变化而修改。
使用者不知道单位时可暂留 null、空字符串或 unknown，不应为了让算法执行而猜填。

2026-10-26 新增的独立指令限位与数值冻结候选沿用此确认规则，配置、源时间证据与升级边界见
[数值检查缺口补齐](NUMERIC_GAPS_20261026.md)。既有评分保留，新子项分别影响覆盖率与筛选。

## 珞石当前配置

配置位于 examples/robots/luoshi_intake.yaml，仍待机主确认。

| 记录 | 当前处理 | 机主需说明 |
|---|---|---|
| 双臂 position | 原始 14 维及左右各 7 维视图；rad 为文档候选，标定 pending | 顺序、角度单位、零位与校准 |
| effector/position | 用户确认设备是灵巧手；other/recorded_values、unit: null | 两维记录具体含义、完整 DOF 与布局；不自动解释为左右夹爪 |
| head/position | other/recorded_values、unit: null | 每个分量是转动/倾角还是伸缩 |
| arm/velocity | other/recorded_values、unit: unknown | 速度还是加速度；角量还是线量及单位 |
| arm/torque | other/recorded_values、unit: unknown | 是否确为力矩、来源和单位，不能以电流代替 |

随附 HDF5_FORMAT.md 将 effector 称作夹爪，并把 torque/velocity 的单位与解释写反；
这些与用户确认存在冲突，因此不把来源说明当作已经完成的机主确认。
两个手部虚拟夹爪通道已撤掉；数组长度不证明灵巧手 DOF。旧结果保留原始历史，不原地重写。

## 留空会发生什么

1. 原始数据仍可读取，IR 保存实际声明，不用 SI 建议补填源单位。
2. 明确受支持的 deg、mm 等在执行视图读取时转成 rad、m，换算保留映射与证据。
3. 缺单位、未确认物理量或错误量纲的物理输入明确阻塞；说明通道、字段、要求单位及当前声明。
4. Numeric_Check 仍可在原始记录上查 NaN/+Inf/−Inf，但这些原始值不送入物理阈值方法。
   报告标“仅部分检查”，assessment_complete 为 false；并保留具体 physical_check_blockers。
5. 单位未知不代表坏数据；处理阻塞不自动生成排除或建议复核，有限值也不代表合格。

例如某头部 position 确认为伸缩、单位 m：可以进入关节位置物理检查，检测目标单位是 m，
相邻样本差阈值也按 m 解释。阈值数值保持配置值，不因字段是米制而自动缩放；
默认阈值仍来自角度标定，不能当作已标定的米制阈值。
未知量纲或既不是 rad 也不是 m 的关节位置仍明确阻塞，没有“风险自负”绕过选项。
只填写单位也不代替物理量、指令/反馈、坐标系和校准确认。

## 混合头部数组的配置例子

以下仅是“使用者已确认分量 0 为倾角 deg、分量 1 为伸缩 mm”的假设布局，
不是珞石真实布局。实际接入必须以机主说明为准，不能直接复制索引。

~~~yaml
channels:
  head_raw:
    modality: other
    fields: [recorded_values]
    unit: null
    body_part: head
    source_kind: feedback
    representation: {quantity: unknown}
  head_tilt:
    modality: joint_space
    fields: [position]
    unit: deg
    body_part: head
    source_kind: feedback
    select: {channel: head_raw, field: recorded_values, indices: [0]}
  head_extension:
    modality: joint_space
    fields: [position]
    unit: mm
    body_part: head
    source_kind: feedback
    select: {channel: head_raw, field: recorded_values, indices: [1]}
~~~

执行视图分别输出 rad 和 m；原数组不变。若项目声明 parts，两个通道也需关联相应 robot_part/DOF。
Numeric_Check 对角度字段使用 rad、对直线/伸缩字段使用 m；报告中的限位、相邻样本差和冻结项
都带各自字段的 SI 单位。两类的阈值数值保持配置值，不自动换算，也不把米当作弧度。

## 使用者查看推荐池

~~~bash
edc recommend /path/to/data --profile /path/to/robot.yaml
edc inspect /path/to/data --profile /path/to/robot.yaml
edc recommend /path/to/data --profile /path/to/robot.yaml --params params.json --unit-policy manual
~~~

~~~python
from edc import open_dataset, recommend
with open_dataset('/path/to/data', profile='/path/to/robot.yaml') as dataset:
    pool = recommend(dataset, unit_policy='auto')
    for episode in pool['episodes']:
        print(episode.get('recommended_algorithms', []))
        print(episode.get('partial_algorithms', []))
        for item in episode['algorithms']:
            print(item['algorithm']['id'], item['availability'], item['reason'])
~~~

推荐依据是当前通道、已声明单位/物理量、算法预检、标定与环境依赖；不运行检测或模型，
payload_validated 为 false，不表示数据质量合格。available 表示满足已声明的前置条件；
partial 表示只有部分检查可用；unavailable 保留 missing_input、needs_calibration、
missing_dependency 或 processing_error 的具体原因。独立算法不会被无关字段的单位未知阻塞。

run 保存所有已注册算法的推荐快照；所选算法使用本次参数，其余算法按默认参数预检。
如需比较其它参数，应独立运行 recommend 并提供相应 params.json。
推荐快照由 catalog 和 run.json 发现，固定 HTML 在“参数与单位确认”中展示；
历史运行缺少推荐快照时显示未记录，不按当前环境重算历史适用性。

## 按本次参数筛选算法：2026-10-20

算法池现在把方法参数与输入要求一起登记。使用者提供机器人配置和参数后，
推荐与正式执行使用同一套默认参数解析、参数校验及输入预检。改变通道、左右侧或模式后须重新筛选，
不能沿用另一组参数的“可用”结论。算法池可读取数据元信息，但不执行质量方法或加载模型权重；
有效载荷的完整性仍由实际执行验证。

每项包含：

| 登记内容 | 使用者看到的内容 |
|---|---|
| parameter_schema、parameters | 参数类型、范围、枚举、默认值、说明及本次生效值 |
| data_requirements、input_requirements | 具体通道/字段、模态、所需 SI 单位、反馈/指令、标定、时间来源等 |
| requirement_checks、input_descriptions | 每条声明是否满足、未启用的条件输入、算法自有复杂预检说明 |
| dependencies、device、calibration | 当前环境依赖和标定要求；未满足时显示原因 |
| availability、reason、inputs | 可用、仅部分检查、不可用；当前范围与实际绑定的输入 |

例如 params.json：

~~~json
{
  "Action_Smoothness": {"side": "right"},
  "Dedup_Check": {"dedup_ratio_low": 0.01, "dedup_ratio_high": 0.05},
  "Video_Integrity": {"sample_step": 1, "downscale": 4}
}
~~~

~~~bash
edc recommend /path/to/data --profile /path/to/robot.yaml --params params.json
edc run /path/to/data --profile /path/to/robot.yaml \
  --algorithm Action_Smoothness --algorithm Dedup_Check --algorithm Video_Integrity \
  --params params.json --output /path/to/new-results
~~~

同一个 JSON 可用于推荐与上述选定算法执行。run 的参数只接受本次选中的算法 ID；
未选算法仍按默认值记录推荐快照。示例的逐帧参数为显式 1，空间宽高各缩小 4 倍；
Video_Integrity 既有默认值仍为 sample_step=5、downscale=4，未悄悄更换。
Reset 的 reference/tolerance 等机器人标定复用 robot.yaml，不在算法参数中重复登记。

轻量参数规则支持对象属性、必填项、额外参数开关、类型、顶层默认值、枚举、数值范围与数组项；
不是完整 JSON Schema 引擎。不支持的 schema 关键字在插件注册时明确失败，
无 schema 的历史插件保留原有参数行为。描述性 parameters 字符串不会当作默认值传入算法。

## 算法开发者声明物理输入

固定绑定通过向后兼容的可选 AlgorithmSpec 字段声明：

~~~python
from edc.algorithms.registry import AlgorithmSpec
spec = AlgorithmSpec('myteam__angular_check', '1', '角度检测', 'myteam', '说明',
    input_requirements=({'channel': 'left_arm_position', 'field': 'position', 'unit': 'rad'},))
~~~

unit 必须为所需 SI 单位。共享预检检查字段存在、单位/语义已确认、量纲匹配和 auto/manual 策略。
按参数选择通道及条件输入可用新增 data_requirements 声明：

~~~python
from edc.algorithms.registry import AlgorithmSpec

spec = AlgorithmSpec(
    'myteam__position_check', '1', '位置方法', 'myteam', '描述实际方法',
    parameter_schema={
        'type': 'object', 'additionalProperties': False,
        'required': ['channel'],
        'properties': {
            'channel': {'type': 'string'},
            'require_physics': {'type': 'boolean', 'default': True},
        },
    },
    data_requirements=(
        {'id': 'position', 'channel_param': 'channel',
         'field': 'position', 'min_samples': 2},
        {'id': 'physics', 'channel_param': 'channel',
         'field': 'position', 'modality': 'joint_space', 'unit': 'rad',
         'source_kind': 'feedback', 'calibration_status': 'confirmed',
         'time_sources': ['acquisition'],
         'when': {'require_physics': True}},
    ),
)
~~~

这是输入契约示例，不是新质量判定方法。配置对应通道源单位为 deg 时，auto 执行视图提供 rad；
单位未知会阻塞物理模式，不能以数值大小猜测。require_physics=False 只跳过此示例的物理需求，
供原始有限值等无单位操作使用；不能让实际物理方法绕过 SI 要求。
可安装示例见 [configured_position.py](../../examples/plugins/algorithm/configured_position.py)。

目前 data_requirements 支持固定 channel 或参数指定 channel_param（字符串或字符串数组）、modality、field、SI unit、
source_kind、calibration_status、time_sources、clock_required、min_samples、min_channels、frames_required 和 when 条件。
指标池的数组参数会逐通道检查并记录逻辑名到实际来源的绑定；`required_channels` 和 `field_map` 同样解析逻辑名。没有显式通道选择时，检查状态为 deferred，实际默认范围由指标自身预检确定并记入绑定；没有默认范围预检的插件会缺输入，不会把未检查的数据误报为已满足。
clock_required 只检查单路时钟声明，跨路共同时钟/映射仍须算法专有预检，不能把多个非空 clock 当作同一时钟。
RGB 声明默认需要可读图像；纯时间类指标显式使用 frames_required=false，只要求时间元信息，不能据此宣称检查了画面。
复杂相机映射、坐标系、跨路关系和多种可选输入继续在插件 preflight(episode, params) 中明确校验，
成功返回 ('runnable', '')，失败返回状态和原因。模型/GPU 仍延迟加载，不在预检中执行。
部分范围可通过可选 availability(episode, params) 返回 availability: partial、reason、scope、fields。
钩子异常对该算法单独记录；不影响其它算法。不需修改核心 ID 分发表。
插件可按其实现声明具体输入；未声明具体物理输入的插件只通过既有能力检查，不能据此宣称完整物理适配。

## 原始时间与去重范围

HDF5/LeRobot 的公共时间字段保留原值、单位及来源，只检查一次，报告明确显示“公共时间字段”。
它能用于原值相等检查；单位和来源明确时可观察单轴间隔，不能证明各传感器原始采集同步。
未知单位、生成时间或缺原始时钟会在对应算法中列出限制；不复制公共时间轴伪装成独立时间。

时间值 [100,110,110,120,120,120]：110 涉及两条、首次以外 1 次；120 涉及三条、首次以外 2 次，
所以共涉及 5 条，额外出现 3 次。两条连续相同和非相邻复用现在也保留行证据，但不实际删除。
旧 duplicate_count/dedup_ratio 仍按连续至少五条的整段记录数及原阈值评分；
短段/非相邻新增范围未配置判定标准时为 OBSERVATION，评估不完整，不宣称通过。
缺时间的通道单列未评估，默认全通道推荐为部分可用；右主臂全零 header 不当作 100% 时间重复。
单通道显式选择只判断该通道的可用范围。不同通道具有相同时间不在此项中互相去重，图像内容重复属于另一问题。


迁移前真实样例的历史报告保留在原研究仓库，不随本 CPU 库分发；当前验收见 [STATUS](STATUS.md)。

## 姿态自动统一（0.2.0a2）

普通中间层读取和算法输入的 `orientation` 均为 N×4 XYZW 单位四元数；源 RPY
角度换算属于姿态表示转换，不再把四元数分量作为角度重复换算。此规则在 auto/manual
单位策略下都生效。源表示不明或旋转非法时阻塞姿态读取，保留原始诊断入口和其他通道。
每次执行写入 `orientation-normalization.json`，只在实际读取成功时标记已转换。
迁移和边界见 [姿态契约](ORIENTATION_HAND_TACTILE.md)。
