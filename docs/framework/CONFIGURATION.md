# 统一配置

EDC（Embodied Data Curation）使用一个总入口，引用数据集、机器人、算法和工作流。`edc config export` 将引用展开成可独立使用的单文件；相对路径以各自子配置文件为基准解析，运行保存完整生效快照和数据来源指纹。

```yaml
schema_version: edc_config_v1
unit_policy: auto
dataset: {ref: datasets/microwave.yaml}
robot: {ref: robots/x1_pro.yaml}
algorithms: {ref: algorithms/quality.yaml}
workflow: {ref: workflows/curation.yaml}
```

`ref` 不接受同时附带覆盖键；循环引用失败。不同运行可以引用同一机器人，不复制填写相同语义。

## 数据集端

```yaml
path: ../../data/microwave
id: microwave_batch_01
version: '1'
backend: auto
tasks: [microwave]
episodes:
  - id: demonstration_001
    path: recording.mcap
    format: mcap
    selector: {}
```

`episodes` 是可选的显式索引，不按 LeRobot 文件数量推断边界。不提供时使用读取器及数据集 metadata 的边界。显式轨迹必须真实存在于 `path` 目录中，ID 唯一，字段 selector 由读取器解释；单个 MCAP 文件也可直接作为 `path`。

- `source_bindings: {joint/arm/main/feedback: /recorded/joints}`：本批次源字段改名，逻辑名与机器人语义保持一致。不能把两个信号合并成同一逻辑名。
- `capture` 保存本批次的单位、时间来源、clock、标定和变换。填写 `scope` 说明适用批次；`capture.channels` 的键使用源名或逻辑名，补充单位与时间字段；`capture.transforms` 和 `capture.calibration` 提供本批次完整声明。
- `backend` 使用 `auto/official/compat` 选择读取实现；依赖缺失时不偷偷切换。数据格式支持不代替机器人或算法验收。

## 机器人端

[完整 X1 Pro](../../configs/robots/x1_pro.yaml) 和 [独立单臂](../../configs/robots/single_arm.yaml) 为入口。型号 `model` 与工程设备代号 `instance_id` 分开；工程代号未确认则保持空。

```yaml
schema_version: 1
id: single_arm
version: '1'
model: Example arm
instance_id: cell_01_arm_03
parts:
  main: {type: arm, side: unknown, dof: 6}
  tool: {type: gripper, side: unknown, arm: main}
channels:
  /joint_states:
    robot_part: main
    logical_name: joint/arm/main/feedback
    modality: joint_space
    fields: [position, velocity, effort]
    source_kind: feedback
    field_metadata:
      position: {unit: deg, quantity: joint_angle, semantic_status: confirmed}
      velocity: {unit: null, quantity: unknown, semantic_status: unknown}
      effort: {unit: null, quantity: unknown, semantic_status: unknown}
```

示例中的已确认 position 只适用于确认其实际源单位的情况；不能照抄成自己的确认。`dof` 可暂不填写；填写后核对真实维数。未声明 `type: arm` 不推断机械臂拓扑。`side` 只是物理位置，不承担部件身份。

`robot_part` 关联关节、末端和腕相机；工具部件通过 `arm` 关联其机械臂。共享头部／胸前相机无须归入某条臂。未知字段保持未知，logical_name 不会补出单位、指令／反馈或标定。

确认可带 `applicability: {dataset_ids: [batch_a], instance_id: cell_01_arm_03}`；运行核对适用数据集与设备，不符合即失败。批次专用时钟和标定放在 dataset.capture 中；不以“同一型号”证明可以复用。机器人配置不允许 `path/tasks/episodes/dataset/allowed_tasks`。

## 算法端

```yaml
defaults:
  arms: all
  selection: runnable
methods:
  Action_Smoothness:
    arms: [left]
    params: {}
  Video_Integrity:
    params: {input_mode: auto, sample_step: 1, downscale: 4}
  Sync_Check:
    params: {primary: rgb/head}
```

- `arms: all` 选择机器人声明的全部臂；独立单臂为 `main`，双臂为 `left/right`。单项 `arms` 覆盖默认。不存在的 ID、空列表、左右冲突与缺失逻辑名明确失败。
- 参数 `channels` 是**算法输入范围**，使用逻辑名或源名；不改变数据存储。所选臂之外的通道不能借显式配置重新进入。
- `selection: runnable` 默认执行 available 和 partial；不可用项保留原因。`explicit` 对所有显式选定方法记录执行／阻塞状态。部分评估不表示整个所选范围达标。
- `input_mode` 是用户在算法配置中选择的媒体读取方式：`auto` 按读取器声明选择；图片／内嵌 JPEG 走 frames，原编码视频走 video。算法开发者声明支持的入口；用户不需要转存数据。`sample_step` 和 `downscale` 分别控制时间抽样和空间缩放。
- `unit_policy: auto` 在执行层将确认的源值换成 SI；存储不变。manual 关闭自动换算，调用方须准备并如实声明 SI。不会因为选了某算法就自动更改其阈值。

`target_clock` 是**时间算法参数**；`primary` 只是参考通道。选择 `rgb/head` 不等于把所有通道改成头部时钟。只有数据侧明确了源 clock 和可验证映射后，`target_clock` 才可使用；原始采集时间和频率保留，不重采样。映射格式及已有测试见 [时间与单位](READING_UNITS_AND_TIME.md)、[变换接口](TRANSFORMS.md)。

## 工作流端及交接

```yaml
id: curation
stages:
  - {id: quality, kind: quality, input: source, algorithms: recommended}
  - id: edit
    kind: editing
    input: quality
    algorithms: [my_team__editor]
    select: {all: true}
    when: needs_editing
  - {id: recheck, kind: quality, input: edit, algorithms: recommended}
  - id: value
    kind: value
    input: recheck
    algorithms: [my_team__value]
    method_select: {my_team__value: [episode_001]}
  - id: augment
    kind: augmentation
    input: value
    algorithms: [my_team__augment]
    select: {all: true}
  - {id: augment_recheck, kind: quality, input: augment, algorithms: recommended}
```

安装对应真实插件后才可使用上述编辑、价值和增强 ID。`recommended` 按当前阶段类别选择已安装生产方法，排除 `example: true` 的 SDK 示例。没有真实方法时，该阶段挂起并说明原因。

每个 quality 阶段的算法按配置顺序调用。对于只接受一个输入的平滑度，适配层按所选臂生成实例，记录同一算法 ID、不同 `instance_id`、实际绑定与各自结果，不生成跨臂平均分。

`when: needs_editing` 只编辑明确失败的样本；没有选中失败样本则跳过编辑。已达标样本保留，与通过一致性校验的编辑副本共同进入重新质量检查。缺输入和待复核不自动触发编辑。省略 when 表示显式对所选样本执行配置的编辑，适合已明确需要变换的任务。

价值阶段先按质量 keep 筛选，再分别检查各价值方法的条件；dataset 方法使用自己的 `episode_requirements`／`preflight_episode` 和 `preflight_dataset`，每个方法在自己的集合上执行一次。`method_select` 进一步限制各方法的轨迹，不能绕过质量或输入条件。`select` 支持 `all:true` 或显式 `episodes`，还可通过 `channels` 限制评估通道；selector 与引用保存此范围。变换必须保留完整轨迹，用插件参数说明修改哪些通道。

阶段结果含数据引用、run ID、记录、质量放行、所选轨迹和证据；只读 `context.upstream` 提供上游信息。变换另返回 output/params/source_mapping，自动校验后才获得可供复检的引用。

`status` 表示阶段处理是否完成；`delivery_status=ready/suspended` 表示是否可交付。`outputs` 给出终端数据引用、放行轨迹和质量状态，`pending` 给出挂起原因。编辑／增强新版本清除原版本质量资格，必须复检；复核后用 `--resume` 重算推荐与放行，并只复用身份、参数、版本和证据均匹配的运行。
