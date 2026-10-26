# 算法参数与输入契约

本库范围为 9 项 CPU 方法，最新验收见 [STATUS](STATUS.md)。迁移前包含 GPU 方法的历史 11 项记录不作为本库验收结论。

- `AlgorithmSpec.parameter_schema` 声明可配置参数；`data_requirements` 声明随参数生效的字段、SI、语义、时间和最少样本要求。
- `preflight` 校验复杂关系，推荐与执行共用绑定；缺输入／缺依赖／待标定／处理错误与数据判断独立。
- 逻辑名解析适用于外部插件的 channel_param，避免预检能找到而 run 仍拿逻辑名查原 topic。
- `dataset` 方法对所选集合执行一次，不按 episode 重复；`context.upstream` 提供只读阶段引用。

可安装例子：[configured_position.py](../../examples/plugins/algorithm/configured_position.py)。操作见 [ONBOARDING](ONBOARDING.md)、[WORKFLOWS](WORKFLOWS.md)。
