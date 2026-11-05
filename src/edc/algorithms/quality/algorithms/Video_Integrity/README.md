# A 视频完整性 (video_integrity)

## 公共框架输入配置

使用者在算法参数文件的 `Video_Integrity` 下填写 `channels` 和 `input_mode`，
不是在机器人身份配置中选择图片／视频。默认 `auto` 按读取器逐通道声明的实际存储选择：
`frames` 直接读取 RGB，不生成 MP4，复用现有黑／白屏、花屏、连续冻结、遮挡、模糊、曝光和偏色检测；
`video` 保留原视频健康与容器检查。frames 不适用容器子项，顶层分数为空，不能当成完整视频检查通过。
LeRobot image／video feature 可共存；只有 Video_Integrity 的公共适配器支持该参数，
下述独立视频 CLI 接收视频路径。配置、源映射与输出字段见
[统一配置](../../../../../../docs/framework/CONFIGURATION.md#算法端)；
本文其余部分保留历史方法说明，实际当前画面实现以 `FrameQualityDetector` 为准。

两层检测, 均不依赖 AI 模型 / GPU:
- **时序/容器层** (ffprobe/ffmpeg): 黑屏亮度 / 丢帧 / 帧率不稳 / 卡顿(重复帧) → 4 原子打分
- **画面层** (OpenCV + numpy, CPU): 黑屏 / 白屏 / 花屏 → 画面严重异常则整模块熔断

## 用法

随 `edc batch` 运行；框架经 `inputs.py` 调用 `method.assess`，参数写在机器人 yaml 的 `checks.Video_Integrity`。

## 输入 / 输出

**输入**: 单个视频文件 (cv2/ffmpeg 可解码的 mp4/avi/mkv 等), `cam_type` 仅用于审计 (head/wrist 阈值相同)。

**输出** (统一契约 + 两个明细字段):
```json
{
  "module": "video_integrity", "score": 100, "grade": "S", "passed": true,
  "verdict": "ACCEPTABLE", "reasons": [],
  "sub_indicators": [ /* 时序 4 原子: 黑屏检测/帧完整性/帧率稳定/画面冻结 */ ],
  "health_check": { "is_healthy": true, "issues": [], "black_screen": {...}, "frame_drops": {...}, ... },
  "frame_quality": { "black_ratio": 0, "white_ratio": 0, "snow_ratio": 0, "score": 100, "verdict": "ACCEPTABLE", "per_frame": [...] }
}
```
> 任一时序原子 = 0 或 画面质量熔断 (黑/白/花占比 ≥ 50%) → `verdict = DO_NOT_USE`。

## 时序检测项 (4 原子, ffprobe/ffmpeg, camera-agnostic)

| 原子 | t_min | t_max | direction | 说明 |
|---|---|---|---|---|
| 黑屏检测 | 15.0 | 50.0 | forward | 平均亮度<15→熔断, ≥50→满分 |
| 帧完整性 | 0.02 | 0.0 | inverse | 丢帧率>2%→熔断, 0→满分 |
| 帧率稳定 | 10.0 | 0.0 | inverse | PTS std>10ms→熔断, 0→满分 |
| 画面冻结 | 0.15 | 0.0 | inverse | 重复率>15%→熔断, 0→满分 |

## 画面质量检测 (CPU: 黑屏/白屏/花屏)

`frame_quality.py::FrameQualityDetector`。用 cv2 均匀抽 N 帧 (默认 20), 每帧转灰度缩放到 128×128, 计算:

| 异常 | 判据 (单帧) | 物理含义 |
|---|---|---|
| **黑屏** | 平均亮度 `mean < 15` | 画面丢失/过暗 |
| **白屏** | `mean > 240` 且 `std < 8` | 近乎纯白, 无细节 |
| **花屏** | 频谱平坦度 `flatness > 0.15` | 高频随机噪声主导 (雪花/块效应/解码损坏) |

**频谱平坦度** = 高频区能量密度 ÷ 低频区能量密度 (FFT, 面积归一化, `fft_radius=0.18`):
- 正常画面能量集中于低频 → flatness 低
- 花屏/噪声频谱趋平 (白噪声) → flatness 高

阈值校准 (128×128 灰度):

| 视频类型 | flatness 范围 | 结论 |
|---|---|---|
| 正常 (faceImg/leftImg/rightImg) | 0.04 ~ 0.09 | 远低于阈值 |
| 强噪声 (noise=100) | 0.20 ~ 0.29 | 高于阈值 → 花屏 |
| 纯黑/纯白 | 0.00 | 无高频 (被黑/白屏判据捕获) |

**汇总打分**: `worst_ratio = max(black_ratio, white_ratio, snow_ratio)`; `score = 100×(1 − worst/0.5)` (worst=0→100, worst≥0.5→0)。`worst≥0.5` → 画面熔断, 整模块 DO_NOT_USE; `worst>0` → NEEDS_REVIEW。

## 检验流程

1. **正常基线**: 真实视频应 `frame_quality.score≈100`, 三 ratio 均为 0, flatness < 0.09。
2. **黑屏**: `ffmpeg -f lavfi -i color=black:s=640x480:r=20:d=3 black.mp4` → black_ratio=1.0 → 熔断。
3. **白屏**: `ffmpeg -f lavfi -i color=white:s=640x480:r=20:d=3 white.mp4` → white_ratio=1.0 → 熔断。
4. **花屏**: `ffmpeg -i <normal> -vf noise=alls=100 noisy.mp4` → snow_ratio≈1.0 (flatness>0.2) → 熔断。
5. 视频不可解码 → `frame_quality.degraded=true`, 跳过画面检测 (不误判, 由 health_check 处理不可读)。
