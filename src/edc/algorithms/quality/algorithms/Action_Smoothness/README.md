# 动作平滑度（Action_Smoothness）

检测末端位置反馈的静止段残差 jerk，并保留独立的 SLAM 冻结子项。位置经执行层换算为 **m**，逐点时间为 **s**，jerk 为 **m/s³**。不清洗、补值或修改源数据。

## 使用者：算法端配置

```yaml
defaults:
  arms: all
methods:
  Action_Smoothness:
    params:
      timeline:
        mode: reference_channel
        reference_channel: rgb/head
        time_source: acquisition
        resample: linear
        clock_policy: require_common_or_explicit_mapping
      expected_rate_hz: 30
      window_length: 11
      vel_threshold_factor: 0.1
      jerk_threshold_m_s3: 15
```

`arms: all` 按机器人声明分别检查全部机械臂。只检查右臂时，添加 `arms: [right]` 和 `params.channel: pose/arm/right/feedback`；也可用源 topic。参考通道只提供时间，不解码图像，不进入机械臂位置输入选区。

**机器人／数据集端**负责确认单位、反馈语义、acquisition 来源及 clock；算法参数只引用这些声明。不能因为两路时间值接近或选择了头部相机，就填入共同 clock。明确的时钟映射使用 `transforms.clocks`，配置与适用范围见 [变换接口](../../../../../../docs/framework/TRANSFORMS.md)。

## 时间轴规则

| 模式 | 算法实际输入 | 边界 |
|---|---|---|
| `reference_channel` | 源位置线性插值到指定通道的真实 acquisition 时间点 | 两路必须有共同 clock 或直接显式映射；仅取重叠范围，逐行记录来源，不外推 |
| `native`（缺省） | 位置自身已有的 acquisition/container 时间 | 不重采样；不能据此证明跨通道同步 |

使用自身时间轴时明确写 `timeline: {mode: native, time_source: acquisition}`。`expected_rate_hz` **只表示参数标定频率，不生成均匀时间**。默认 30 Hz；实测中位频率偏差超过 `rate_tolerance_ratio`（默认 0.2）时，只保留诊断 jerk，顶层为 `NEEDS_REVIEW`、`score: null`、`assessment_complete: false`。变更这个声明前需有相应参数标定依据。

缺共同 clock／映射时不执行跨轴插值和 jerk，直接待复核。无效或非递增时间、无重叠、映射超适用范围、样本少于滤波窗口同样不产生质量通过结论。未知单位、错误通道和配置冲突在输入预检阻塞。

## 计算方法

1. 在选定评估时间轴上对 XYZ 应用 Savitzky-Golay（默认 11 点、三阶多项式）。
2. 用平滑位置与逐点实际时间计算速度，门限为全局峰值的 `0.1` 倍；完全静止时保留 `1e-6 m/s` 零峰值保护。
3. 按原方法进行低速段膨胀及腐蚀／膨胀。膨胀可能纳入边界运动帧，报告分别保留原低速帧、额外纳入帧及其 jerk 证据。
4. 对 **原位置减平滑位置** 的残差计算三阶导；每一阶传入完整逐点时间，不用平均／中位间隔替代。
5. 统计静止掩码内 jerk 幅值 RMS，使用原 inverse 评分公式。默认 5 m/s³ 为满分基线，15 m/s³ 为熔断线；显式阈值进入同一公式，不另建评分方法。
6. 在同一评估输入上执行原 SLAM 冻结检查；冻结前运动及持续时间条件保持原实现。无可评估静止段不视为满分。

11 点窗口在 250 Hz 与 30 Hz 上覆盖的时间不同，不能直接用同一标定阈值解释两种输入。参考模式保留实际相机抖动和间隔，不暗中再均匀化。

## 输出与证据

`dqs_record_v1` 分开保存执行状态和质量判断。`raw.jerk` 保留原始 RMS、峰值、静止帧与分段信息；`raw.slam_freeze` 保留冻结证据。频率不匹配时，候选评分仅在 `diagnostic_result` 内留存，不参与放行。

每个实例的 `timeline.json` 随 catalog 发现，保存：源／参考通道、原始秒制时间、clock 状态与映射参数、参考保留／排除行、目标时间、插值左右源行和权重、时间范围、参数标定频率、实测间隔及窗口时长。clock 映射与重采样分别记录；使用已声明 clock 不代表本方法验证了硬件同步。

开发者可调用 `run_check.assess_arrays` 在已准备好的 SI 数组上复核计算；它是底层方法接口，不负责机器人配置、clock 或标定频率放行。正式运行与推荐均经过 EDC 适配器。
