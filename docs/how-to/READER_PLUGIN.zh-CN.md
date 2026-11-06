# 读取器扩展

[返回主页](../../README.zh-CN.md) · [English](READER_PLUGIN.md) · [输出对象与接口参考](../reference/OUTPUTS.zh-CN.md)

读取器插件通过 `edc.readers` 入口，将数据格式映射为 数据集（`Dataset`）→ 轨迹（`Episode`）→ 通道（`Channel`）。
读取器接口的契约条目见[输出对象与接口参考](../reference/OUTPUTS.zh-CN.md#readers)；数据模型与边界见[框架结构与设计取舍](../explanation/ARCHITECTURE.zh-CN.md)。

使用已有格式的新机器人通常只需配置，见[数据与机器人配置](DATA_AND_ROBOT.zh-CN.md)。

## 1. 从模板开始

将 [`manifest_reader.py`](../../examples/plugins/reader/manifest_reader.py) 与其 [`pyproject.toml`](../../examples/plugins/reader/pyproject.toml) 复制到独立插件包，设置包元数据，并将 `edc.readers` 入口指向读取器实现。模板读取自定义清单，使用内部数据模型类；`edc` 当前未导出 `Dataset` 与 `Episode` 的具体构造类。公共数据访问使用 `from edc import open_dataset`。

## 2. 实现两个入口

- `can_open(path)`：只说明这个读取器是否声称能打开该来源，不读取全部内容。
- `open_dataset(path, ...)`：返回统一数据接口，保留 数据集（`Dataset`）→ 轨迹（`Episode`）→ 通道（`Channel`）。

实现要求：

1. 明确每条轨迹的 ID、源文件与选择器（`selector`）；不按文件数猜轨迹数。
2. 区分采集、容器、播放、合成及未知时间；单位换算、时钟映射与重采样分别记录。
3. 未知的语义和单位保留为未知，不用字段名或归一化推断。
4. 图像、数值、时间读取及来源映射须与声明一致。

## 3. 安装并验证

```bash
pip install /path/to/reader-plugin
```

安装后，使用公共接口和匹配的机器人配置打开代表性数据，再按[算法运行](RUN_AND_SELECT.zh-CN.md)执行适用检查，确认预检与执行均能发现该读取器。验证覆盖：

- 可用输入、缺输入、损坏文件与读取失败分别有明确结果；
- 多段操作的边界来自元数据或选择器，不来自文件数量；
- 来源映射与原始记录可追溯，原始数据保持只读；
- 读取器不会把播放时间或合成时间当作采集时间。

相关测试与安装要求见[贡献指南](../../CONTRIBUTING.md)。

## 相关页面

- [输出对象与接口参考](../reference/OUTPUTS.zh-CN.md#readers)：读取器接口契约。
- [数据格式与机器人配置参考](../reference/DATA_AND_ROBOTS.zh-CN.md)：已有格式与配置。
- [框架结构与设计取舍](../explanation/ARCHITECTURE.zh-CN.md)：为什么数据按 数据集（`Dataset`）→ 轨迹（`Episode`）→ 通道（`Channel`） 组织。
