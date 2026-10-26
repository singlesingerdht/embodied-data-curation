# 读取、单位与时间

统一配置见 [CONFIGURATION](CONFIGURATION.md)。读取端保留存储原值；算法端按已确认的字段语义提供 SI；报告记录实际换算、输入范围和不能执行的条件。框架不清洗、补值、改频率或猜单位。

| 内容 | 配置与执行边界 |
|---|---|
| 关节、灵巧手、头部与升降 | 每字段明确转角／伸缩、源单位、关节顺序和 command／feedback；deg→rad，mm／cm→m；不能把所有 position 当成角度 |
| 速度、加速度、力矩、电流 | 每字段单独登记物理量；deg/s→rad/s，deg/s²→rad/s²，mA→A，N·mm→N·m；电流不自动当力矩 |
| 末端与姿态 | XYZ 单位、坐标系、旋转表示及顺序明确；四元数用无量纲分量，不转为弧度 |
| RGB、视频、深度 | 图像／原视频走相应媒体入口；uint16 深度不自动证明毫米，需比例、单位和无效值规则 |
| 触觉与其他数值 | 保留明确的数值张量；未知原始码只做有限性等诊断，不能假装具备专用物理检测 |
| 标定与阈值 | Home 和容差同时换算；SI 读取不代替标定。阈值保持算法定义，不因换接口擅自调整 |

官方与原生读取都要求 `quantity` 和 `semantic_status: confirmed` 后才允许物理读取。未知字段仍可通过 `inspect_recorded_signal()` 做原值有限性检查，不能经该接口绕过 SI 进行物理计算。NaN、±Inf 的位置保留。

## 三步时间处理

1. **时间单位**：原值 `[1000, 1004] ms` 转 `[1, 1.004] s`，只说明本路间隔 4 ms。
2. **时钟映射**：如果有依据确认 `t_head = t_arm + 0.25 s`，则映射成 `[1.25, 1.254] s`；没有映射就不能比较两路绝对时刻。
3. **样本匹配**：相机 30 Hz 与关节 250 Hz 保留各自频率。需要匹配时按时间和容差保存源行索引；不按行号静默对齐。

算法参数可选 `primary: rgb/head`；它只选参考通道，不证明所有设备共时钟。数据侧登记源 clock 和经过验证的 `transforms.clocks` 后，Check_Timestamp、Sync_Check、Root_Cause 可显式设置 `target_clock`。执行会记录原时间、源／目标 clock、仿射参数和 identity 行对应；不重采样，不把 container 或 synthetic 升级为 acquisition。

```yaml
# 数据侧 capture 或适用范围明确的机器人声明
transforms:
  clocks:
    arm_to_head:
      source: arm_clock
      target: head_clock
      scale: 1.0
      offset_seconds: 0.25  # 示例数，不是 X1 Pro 实测标定
# 算法侧参数
# Sync_Check: {primary: rgb/head, target_clock: head_clock}
```

| 格式 | 时间证据及使用边界 |
|---|---|
| ROS／MCAP | 容器录制时间与各路有效 header 分开；保留整数 ns 原值，算法间隔用 s；header 全零不可用于采集间隔，不拿录制时间替代 |
| HDF5／Parquet／JSON | 明确原时间列和单位；未知单位保留原值，可查相等，不能算实际采集间隔。公共行时间不证明多路独立采集同步 |
| LeRobot | 标准索引时间与原始采集时间分开；标准 timestamp 是秒制索引，不通过声明改成真实 acquisition |
| MP4／派生媒体 | 播放／合成时间用于定位画面；不能证明原始采集同步 |

头部参考不解决右 master 末端全零 header。X1 Pro 的共同 clock 仍未知；六路末端 XYZ 的米制确认也不确认关节单位、深度单位、其他物理量或标定。

大绝对时刻转 float64 秒仍有精度限制；原整数时间用于精确重复检查。通用亚微秒相对基准、多路 HDF5 独立时间绑定及全部传感器编码不在已验收范围。

测试依据：`test_reading_unit_contract.py`、`test_unit_normalization.py`、`test_workflow_inputs.py`、`test_config_native_timeline.py`。读取契约验证不替代实机采集同步或训练收益验证。
