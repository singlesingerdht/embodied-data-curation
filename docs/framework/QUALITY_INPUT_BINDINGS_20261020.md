# CPU 方法的输入绑定

全部 9 项 CPU 方法均支持 `channels` 参数：填写源名或 robot.yaml 中的 logical_name。缺失、空列表、重复绑定或左右冲突均明确失败；不会请求右侧时回退左侧。

| 方法 | 附加选择参数／约束 |
|---|---|
| Structure_Check | 相机要求取 robot.yaml 的 camera_requirements 与 sensor_id/modality |
| Numeric_Check | 只读选定数值字段；物理子项需逐字段确认 |
| Action_Smoothness | channel 或单个 channels；side 校验明确侧别 |
| Check_Timestamp | target_clock 仅应用显式映射 |
| Sync_Check | primary 指参考通道；target_clock 指共同时间域 |
| Root_Cause | families 可显式分组；target_clock 应用映射 |
| Dedup_Check | channel 或 channels；只去重时间／序号 |
| Reset_Detection | 选定通道必须都有已确认 Home 参考 |
| Video_Integrity | input_mode=auto/frames/video，sample_step 与 downscale 分别记录 |

使用者在算法参数中选数据，算法开发者声明需求，机器人配置声明具体信号的物理含义。9 项 JSON 例子见 [quality_logical_channels.json](../../examples/algorithm_params/quality_logical_channels.json)；这些通道名仅为 X1 形态示例，不是每个数据集都存在。只运行一部分算法时同时删除其它算法的参数条目。

[工作流](WORKFLOWS.md) · [当前验收](STATUS.md)。
