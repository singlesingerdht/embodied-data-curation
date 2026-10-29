# EDC 建设与验收计划

框架名 Embodied Data Curation Framework，公共 Python／CLI `edc`，安装包与仓库名 `embodied-data-curation`。公共核心和九项 CPU 质量方法先交付，仓库后续的价值、GPU 与平台模块按统一接口逐项接入。当前事实与实测证据见 [STATUS](STATUS.md)。

## 组织与契约

- `src/edc/{data,robots,algorithms,execution,workflows,reporting}` 为运行核心。
- `configs/{datasets,robots,algorithms,workflows}` 分别描述数据、硬件、算法输入参数和串行顺序；总入口引用或导出完整单文件。
- `parts` 明确机械臂拓扑，默认 all；单臂 main，双臂 left/right，缺侧不静默降级。机器人可被多个任务数据集复用。
- 数据遵守 Dataset→Episode→Channel；源值、来源与时间身份保留，物理方法使用确认的 SI 视图。
- 输入推荐、执行绑定、质量放行与数据集方法子集共享契约；SDK 例程不冒充真实价值方法。

## 工作流目标与实现

[README 流程图](../../README.zh-CN.md#工作流) 为约定。默认运行一轮可运行的质量方法，CPU 与可选 GPU projection alignment 同属质量池；价值评估在质量达标后由使用者选择，并按方法分别选轨迹。显式编辑生成新版本并返回一致性校验、质量检查；增强也须校验和质量复检，未达标挂起。每个变换输出分支必须先复检，再进行其他操作或交付。缺输入和复核通过补充配置／复核及恢复执行回到推荐和放行。执行采用有限阶段，不无限自动修改。

第一阶段验收基础安装、配置、部件范围、方法结果对照、开发接口与完整合成流程。第二阶段逐项接入真实价值／编辑／增强方法，核对输入、环境、原始输出、资源和许可。第三阶段按现场需求讨论平台与真实 VR 数据加速，不默认不同加速方案等同。

## 仍需推进

真实价值算法效果、非 identity 变换、任意片段调度、平台权限与服务接口，以及代码／算法／模型／真实样例的发布许可分别验收。三人按数据接入、算法插件、完整工作流分工，统一基线与交付物见 [FRIEND_VALIDATION](../../FRIEND_VALIDATION.md)；维护者把关公共输入、方法语义、放行和真实验证口径。公开发布前核查 [清单](../RELEASE_CHECKLIST.md)。
