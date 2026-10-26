# 视频完整性：图像／视频双入口与耗时

## 接入方式

`Video_Integrity` 的公共适配版本为 `ir-adapter-11`。`input_mode` 支持：

| 参数 | 读取方式 | 检查范围 |
|---|---|---|
| `auto`（新默认） | 根据读取器声明的原生存储逐通道选择 | 内嵌图像用 frames；原编码视频用 video |
| `frames` | `Episode.iter_frames()` 顺序读取 RGB 图像 | 原有画面质量方法，不生成 MP4 |
| `video` | 保留原有视频入口，必要时物化视频 | 原有画面方法＋视频容器健康检查 |

这是同一算法的两种输入接口，不是要求两路相机。`channels` 仍可选择多路，
可填写源名称或 `rgb/head`、`rgb/chest`、`rgb/wrist/left` 等统一名称。
机器人位置只读取机器人配置；胸前不会改成腕部。

```json
{
  "Video_Integrity": {
    "channels": ["rgb/head", "rgb/chest", "rgb/wrist/left", "rgb/wrist/right"],
    "input_mode": "auto",
    "sample_step": 1,
    "downscale": 4
  }
}
```

CLI 将此 JSON 交给 `--params`。要复现旧视频路径，显式设置 `input_mode=video`。
旧运行记录和原始数据不改写。兼容参数的 `sample_step` 默认仍为 5；本轮正式
测试及示例均明确设为 **1（逐帧）**。`downscale=4` 仍是宽高各每四像素取一点，
不是再次压缩图像，也不是 JPEG 缩小解码；没有通过少看帧或降低既有检测分辨率加速。

### 存储与时间

MCAP／ROS 内嵌图像、HDF5／Parquet 内嵌图像、官方 LeRobot 的 image feature
选择 frames；原生视频文件、LeRobot 的 video feature 选择 video。
LeRobot 本身可以包含图片或视频，不能只按数据集格式决定入口。
`Episode.image_storage(channel)` 委托给读取器，返回 `frames`／`video`／`unknown`；
SI 执行包装也透传该能力。新格式插件可覆盖 `DataReader.image_storage()`。
未知存储在 auto 下明确缺输入，允许用户显式选入口，不自动生成视频来探测。

图像画面方法不依赖时间戳，源时间未知仍能逐帧检查黑白屏、花屏、冻结、遮挡、
模糊、曝光、偏色。它不因此获得采集节奏、跨路同步或视频 PTS 检查能力。
视频物化仍要求显式 FPS 或可用时间轴。

## 结果与覆盖范围

图像与视频共用原有 `FrameQualityDetector` 计算循环、评分公式和阈值。
图像入口额外要求连续零基源索引、非空 uint8 RGB 和准确帧数；损坏／缺帧明确
处理失败，单相机失败隔离，失败不会记为质量复核或通过。

图像入口保存完整 `frame_quality`，原有已测异常保留；顶层 `score=null`、
`assessment_complete=false`，`container_checks.status=not_applicable`。
推荐池标注“仅部分检查”，理由是没有视频容器，不生成完整视频综合分。
纯图像本身没有需要补造的 MP4；需要原始采集时间检查时运行对应时间算法。

原视频仍检查视频健康项。若检查对象是派生 MP4，结果继续明确为 `derived_preview`，
不能证明源 JPEG 编码完整性。frames 的像素解码成功也不等于独立原编码字节验收。
源 JPEG 与有损派生 MP4 的像素可能不同：本轮不承诺这两种输入的结果逐值一致；
**相同解码像素**的两种入口结果才要求完全一致。

## 验收范围

本库保留双入口和同像素对照测试，不分发历史真实 X1 耗时报告。当前安装与正确性验收见 [STATUS](STATUS.md)。图像入口不进行视频编码，容器覆盖范围也不同；不能只以耗时宣称同覆盖加速。GPU 视觉与投影方法不在本库范围。
