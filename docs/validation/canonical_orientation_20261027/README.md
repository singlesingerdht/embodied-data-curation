# 0.2.0a2 姿态自动统一验收

公共 orientation 读取统一为 XYZW 单位四元数。源 RPY/矩阵/其他分量顺序由明确
配置转换；异常或未知编码不回退为非统一输出。原值和原元数据有独立审计入口。
契约与迁移见 [说明](../../framework/ORIENTATION_HAND_TACTILE.md)。

| 验证 | 结果 |
|---|---|
| 源码全量 | 672 passed / 23 skipped |
| 隔离暂存目录打包、源码外安装 wheel 全量 | 672 passed / 23 skipped |
| 安装 wheel 的官方 LeRobot/姿态/媒体专项 | 135 passed / 3 skipped |
| 干净基础安装 | pip + NumPy + PyYAML + embodied-data-curation；无 Torch |
| 真实 odometry 只读验证 | 2,414×4 XYZW；源内容及时间轴不变 |

各测试集合重叠，不相加。跳过清单保留在日志；真实 MCAP 原始数据、数值样本和
详细元数据不随此目录分发，real-summary.json 仅保存聚合验收结论。
真实验证只覆盖一个已授权本地样例，不证明真实 IMU/手部/触觉物理质量。

package.json 记录已验证 wheel / sdist 的名称和 SHA-256。打包完成后只追加本目录
及 STATUS 说明，不再改运行时代码；复建 sdist 的文档内容和摘要会随之不同。

在安装格式与测试依赖的干净源码目录复现：

```bash
pip install -e '.[formats,test]'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python scripts/validate_orientation_release.py \
  --output /tmp/edc-orientation-validation
```

官方 LeRobot 专项需另建含该可选依赖的环境，并提供
`--official-python /path/to/official-env/bin/python`；不提供时脚本明确记录未执行。
原始 MCAP 不在仓库内，真实样例验证不作为可公开复现的合成测试。
