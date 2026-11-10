# 视频完整性（Video_Integrity）

检查选定 RGB 通道的画面质量；对于视频输入，同时检查视频容器健康。每路相机单独保留结果与证据，不要求 GPU 或模型。

## 输入与运行

提供可解码的 RGB 帧或视频，通过 EDC 数据读取接口运行。深度图不作为 RGB 输入。

在仓库根目录运行，将示例路径替换为实际数据、机器人配置和输出位置：

```bash
edc run /path/to/dataset --robot /path/to/robot.yaml --algorithm Video_Integrity --output /path/to/results/video-integrity
```

在机器人 YAML 的 `checks.Video_Integrity` 中配置检查参数，或通过算法参数文件／Python `params` 传入：

```yaml
checks:
  Video_Integrity:
    input_mode: auto
    sample_step: 5
    downscale: 4
```

| 参数 | 默认值 | 含义 |
|---|---|---|
| `channels` | 由检查范围选择 | 指定 RGB 通道或配置中的逻辑名 |
| `input_mode` | `auto` | 按读取器声明的原生存储选择；可显式指定 `frames` 或 `video` |
| `sample_step` | `5` | 每隔多少帧抽检；设为 `1` 才是逐帧检查 |
| `downscale` | `4` | 宽高各自缩小倍数；设为 `1` 保持原分辨率 |
| `freeze_motion_review` | 空映射 | 将冻结候选关联到明确配置的运动反馈，提供复核线索 |

`frames` 直接读取图像，不编码整段 MP4，也不执行视频容器检查；不能设置视频综合分权重。
`video` 使用原视频或按需物化的视频；物化需要明确帧率或有效时间轴，不能据此证明原编码完整性或采集同步。
画面检查使用 OpenCV、NumPy 和 xxhash；视频路径还需要 ffmpeg 与 ffprobe。

## 输出与判断

- 画面结果包含黑屏、白屏、花屏、冻结、遮挡、模糊、曝光和偏色等检测结果、数量、比例和相关帧证据。
- 画面检查的默认排除比例为 `0.3`；参与判断的最严重问题比例达到该值时排除。持续偏色按独立规则处理，完整阈值见输出中的 `frame_quality.thresholds`。
- 低比例异常可以通过并保留数量；方法返回的复核候选会作为 `method_verdict` 保留。框架适配器的最终判断以实际结果为准。
- 图像输入的顶层综合分为 `null`，容器检查标为不适用；完成画面检查不代表完成视频容器检查。
- 视频输入同时保留容器健康检查和画面结果。缺依赖、不可读或执行失败与数据质量结论分别记录。
- 结果保留 episode、通道、源映射、输入模式及抽样尺度。运动联合证据仅供复核，不改变本算法评分或判断。

## 实现与开发入口

- `algorithm.py`：算法声明、参数与输入检查。
- `inputs.py`：选择图像／视频入口，连接 EDC 数据接口与结果。
- `method.py`：单视频检查入口。
- `frame_quality.py`：画面检查及默认阈值。
- `health_checker.py`、`scoring.py`：视频健康检查与方法内评分。

详细能力与限制见[算法参考](../../../../../../docs/reference/ALGORITHMS.zh-CN.md#video-integrity)，
参数传入方式见[算法运行](../../../../../../docs/how-to/RUN_AND_SELECT.zh-CN.md)，
联合复核见[范围与证据](../../../../../../docs/reference/EVIDENCE_AND_SCOPE.md)。
