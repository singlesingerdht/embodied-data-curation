# 质量算法插件示例

[English](README.en.md) · [接入指南](../../../docs/how-to/ALGORITHM_PLUGIN.zh-CN.md)

`joint_jump` 检查相邻样本的关节角变化，返回逐通道指标、证据和质量判断。示例阈值为 0.2 rad（复核）和 0.5 rad（排除）。

## 安装与运行

在仓库根目录运行：

```bash
python -m pip install -e ./examples/plugins/algorithm
edc algorithms
edc run /path/to/data --robot /path/to/robot.yaml \
  --algorithm team_example__joint_jump --output /path/to/results/joint-jump
```

算法通过 `pyproject.toml` 的 `edc.algorithms` 入口注册。安装后也会加入 `edc batch` 质量集合。

## 文件

| 文件 | 职责 |
|---|---|
| `joint_jump/algorithm.py` | 元数据、通道读取与结果封装 |
| `joint_jump/method.py` | 关节变化计算 |
| `pyproject.toml` | 依赖与插件注册 |

在机器人 YAML 中设置参数：

```yaml
checks:
  team_example__joint_jump:
    review_rad: 0.1
    exclude_rad: 0.5
```

完整开发流程、独立包结构和接口示例见[接入新质量算法](../../../docs/how-to/ALGORITHM_PLUGIN.zh-CN.md)。
