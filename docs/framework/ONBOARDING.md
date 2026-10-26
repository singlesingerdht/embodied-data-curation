# 数据使用者与算法开发者接入

## 使用者：已有格式更换机器人

```bash
dqs robot init --input /data/robot/session.mcap --id my_robot --output /tmp/my_robot.yaml
# 对照设备与采集程序确认；不能把 unknown 批量改为猜测值。
dqs robot check --input /data/robot/session.mcap --profile /tmp/my_robot.yaml
dqs inspect /data/robot/session.mcap --profile /tmp/my_robot.yaml
dqs recommend /data/robot/session.mcap --profile /tmp/my_robot.yaml
dqs run /data/robot/session.mcap --profile /tmp/my_robot.yaml \
  --algorithm Numeric_Check --output /tmp/my_result
```

机器人配置声明原 topic 的逻辑名、部位／侧别、物理量、源单位、指令或反馈、坐标系、标定和时间来源。型号与工程实例代号分别保存；名称不代替语义确认。相机通过 sensor_id、modality、camera_role 声明位置和要求，不从名字猜。

已支持格式主要调整 profile；读取、执行、报告流程继续复用。例：机器人 A 的 `/right_arm/end_pose` 与机器人 B 的 `/robot_b/tool_feedback` 均可声明 `pose/arm/right/feedback`，同一份算法配置可复用；两者仍需各自确认 position 是 m、反馈、坐标系与时间。单一数据集中不同信号不能占用同一逻辑名。

存储原值不变；`dqs run` 默认将明确且已确认的物理输入转换为 SI。`--unit-policy manual` 禁止自动换算并要求调用者准备 SI 数据；未知单位可保留原值有限性检查，物理算法不执行。

时间轴保留原始来源和频率。不同 clock 需要显式映射；选择头部相机为 primary 只指定参考数据，`target_clock` 则调用已有映射，两者不同。详见 [WORKFLOWS](WORKFLOWS.md)、[TRANSFORMS](TRANSFORMS.md)。

## 开发者：算法插件

```python
from dqs.registry import AlgorithmSpec

class MyPositionCheck:
    spec = AlgorithmSpec(
        'myteam__position_check', '0.1.0', '位置检查', 'myteam', '说明方法的实际范围',
        category='quality', granularity='episode',
        parameter_schema={'type': 'object', 'additionalProperties': False,
            'required': ['channel'], 'properties': {'channel': {'type': 'string'}}},
        data_requirements=({'id': 'position', 'channel_param': 'channel',
            'field': 'position', 'unit': 'm', 'source_kind': 'feedback', 'min_samples': 2},))

    def run(self, context, params):
        # channel 已由框架绑定到具体源名，物理数据为 SI。
        values = context.episode.read_signal(params['channel'], 'position')
        context.write_result('shape.json', {'shape': list(values.shape)})
        # 接入自己的原方法；此示例仅观察形状，不表示质量合格。
        return {'verdict': 'OBSERVATION', 'score': None, 'shape': list(values.shape)}

plugin = MyPositionCheck()
```

独立包登记：

```toml
[project.entry-points."dqs.algorithms"]
myteam__position_check = "my_check:plugin"
[tool.setuptools]
py-modules = ["my_check"]
```

安装后自动发现，不修改核心分发表。ID 必须 `team__algorithm`，重复 ID／不兼容接口明确失败。模型与 GPU 延迟加载；预检不下载权重。参数支持轻量 schema，不宣称完整 JSON Schema。

`parameter_schema` 声明参数，`data_requirements` 声明与参数关联的最低输入，复杂关系由 `preflight(episode, params)` 校验。预检和执行共享绑定，SI 与语义门槛对官方／兼容后端一致；物理量不明时不能只靠写一个 rad 获得资格。

支持 episode 和 dataset 粒度；dataset 插件必须实现 `preflight_dataset(dataset, params)`，对当前集合调用一次。四类业务示例见 [算法模板](../../examples/plugins/algorithm)，上游及变换契约见 [WORKFLOWS](WORKFLOWS.md)。首轮没有 segment 调度。

```bash
pip install -e '.[formats,test]'
pip install -e examples/plugins/algorithm
dqs algorithms
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/dqs/test_workflows.py tests/dqs/test_workflow_inputs.py -q
# 卸载发行包后算法从池中消失；历史结果仍可读。
pip uninstall dqs-example-algorithm
```

## 新格式插件与验收边界

新格式使用 `dqs.readers`，实现 `can_open(path)` 和 `open_dataset(path, profile)`，返回可实际取数的 Dataset，不只返回 topic 清单。[读取器模板](../../examples/plugins/reader) 复用既有载荷读取能力。

原生 HDF5、Parquet、MCAP、ROS bag、JSON/episode、MP4 与 LeRobot 的载荷能力分别验收。HDF5 与旁边 MP4 的文件名相似或帧数相同不证明关联，需显式轨迹／帧映射；不得把三个视频文件计作三条机器人轨迹。

[数据模型](DATA_MODEL.md)、[单位与算法池](UNITS_AND_ALGORITHM_POOL.md)、[Protobuf](PROTOBUF.md)、[验收状态](STATUS.md)。历史真实样例及其证据保留在原研究仓库，不随本库分发。
