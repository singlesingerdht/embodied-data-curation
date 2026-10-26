# 仓库后续开发约束

本规范适用于仓库自有代码，第三方子模块按其上游规范维护。建设基线见
`docs/framework/BUILD_PLAN.md`，当前验收状态见 `docs/framework/STATUS.md`。

- 新公共接口由 `dqs` 导出；内部实现归 `framework` 或对应业务目录，自有代码不得经 `dqs` 绕行。`dqs` 仅保留薄入口和兼容转发。数据格式、机器人配置、算法适用范围分别维护，不能以“能打开文件”代替完整支持验收。
- 数据遵守 Dataset → Episode → Channel；轨迹边界以 metadata/显式映射为准，禁止按 LeRobot 文件数推断轨迹数。保留稳定 ID、源文件和 selector。
- 时间来源必须区分 acquisition/container/playback/synthetic/unknown；跨通道比较必须有共同 clock 或显式映射。不得用合成时间证明采集同步。
- 状态与动作、电流与力矩不能自动互换。单位、维度、坐标系、指令/反馈及校准状态明确登记；未知值保持未知。
- 新算法通过 `AlgorithmSpec` 与 `run(context, params)` 注册，安装插件使用 `dqs.algorithms`；新格式插件使用 `dqs.readers`。禁止为新算法修改核心写死分发表。
- 官方算法 ID 保留仓库目录名。外部 ID 使用 `team__algorithm`，接口版本不兼容、重复 ID 明确失败；模型/GPU依赖延迟加载。
- 新结果遵守 `dqs_record_v1`：处理状态与数据判断独立。保留原始评分/事件/分布/实验估值及证据，不生成跨算法总分。变换返回 output/params/source_mapping。
- 新输出由 catalog.json 发现，保存 `_runs/<run_id>` 中的配置、版本、记录、错误及证据。旧入口与旧结果保持兼容，变更必须说明迁移边界。
- 原始数据只读，输出禁止覆盖源轨迹。处理失败不自动成为 exclude/review；不完整评估不能冒充 keep。
- 对契约、边界、插件、失败隔离或结果语义的变更补充有意义的测试。基础安装必须不含私有 wheel、GPU库或模型权重。运行相关测试及安装包验证后更新 STATUS.md。
- 第三方/算法/模型/真实样例分别核查许可和来源。未知许可不能标注已完成开源发布。合成示例不得冒充真实倒茶示教。
