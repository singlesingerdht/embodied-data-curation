# MCAP Protobuf 基础解码

MCAP 容器读取、载荷解码、物理语义映射是三个步骤。此前只完成这份样例的容器读取；
现在 `framework/readers/protobuf.py` 可根据 MCAP schema 内嵌的 FileDescriptorSet 动态创建消息类型，
无需厂家生成的 Python 文件，不按珞石/双臂样例路径写死逻辑。

安装 `pip install -e '.[formats]'`，其中 protobuf 是可选格式依赖，基础安装仍只需 NumPy/PyYAML。
每份 schema 使用独立 descriptor pool，按依赖顺序加载。缺少 schema/依赖、不支持的图像编码，
不宣称可读；reader 的 DataInfo.extra.decode_errors 留存原因。损坏的后续消息不会跳过或补零。

## 数值与图像接口

```python
from dqs import open_dataset

with open_dataset('/path/to/record.mcap', profile='/path/to/robot.yaml') as dataset:
    for episode in dataset.iter_episodes():
        with episode.opened():
            for channel in episode.channels.values():
                if channel.value_available:
                    for field in channel.fields:
                        values = episode.read_signal(channel.name, field)
                        print(channel.name, field, values.shape)
                if channel.image_available:
                    for frame in episode.iter_frames(channel.name):
                        image = frame['image']
```

- 数值：递归读取单个嵌套消息中的标量、枚举和重复数值，保留点分原名，例如
  `joints.position`、`target.position`、`pose.position.x`、`command.adv.ratio`。
  点分名称在 IR 中是实际字段键，不要求算法再次解析 Protobuf。
- 不把缺失的父消息、空关节数组补为零；不同帧缺字段时读取失败，不能静默缩短轨迹。
  Protobuf 本身的无 presence 标量按该协议默认值读取；不能由此证明硬件实际测量过零值。
- 图像：Foxglove RawImage 的 rgb8/bgr8/rgba8/bgra8/mono8/8UC1，尊重 step 行填充并检查尺寸；
  CompressedImage 复用图像解码器。当前不支持 16 位深度、Bayer 等其它像素编码。
- header/timestamp 不进入机器人信号字段；继续使用 MCAP log_time，来源为 container。
  消息内时间尚未映射为 acquisition，不能由名称自动认定采集时钟。
- 暂不处理重复 message、map、字符串任务字段的自动语义映射。数值缓存于内存，图像按需流式解码。

单位、反馈/指令、部位、关节顺序仍通过机器人 YAML 声明。要把 `joints.position` 用作标准
关节 position，可用已有 `select` 显式映射并配置实际 DOF，见 ONBOARDING.md。
此次不猜测原始末端各标量的物理单位、不将 gripper force 自动当作已标定牛顿值。


本库不分发历史真实 MCAP 与实验报告；当前测试与验收见 [STATUS](STATUS.md)。
