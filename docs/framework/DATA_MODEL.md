# 模态、机器人部位与任务元数据

## 三个独立维度

- modality：数据是什么，如 rgb、depth、imu、joint_space、pose、tactile。
- body_part / side：属于哪里，如 hand/left、arm/right、head。
- source_kind：代表指令、反馈、派生结果还是未知。

相机属于设备，RGB 和深度属于不同数据模态。一台 RGB-D 相机的两个 topic 可用相同
sensor_id 关联，但仍保留各自字段、时间轴和读取能力。representation 描述编码、布局、
四元数顺序、触觉阵列形状等；作为可扩展声明保存，不会自动触发解码或数值转换。
末端位姿用 modality: pose，body_part: end_effector；关节指令与反馈都用 joint_space，
通过 source_kind 分开。

## 内置模态词表

| ID | 数据 |
|---|---|
| rgb | RGB 图像 |
| depth | 深度图；保留深度数值及单位，不当成 RGB |
| infrared / thermal | 红外强度／热成像 |
| imu | 加速度、角速度、姿态等 |
| joint_space | 关节位置、速度、电流、力矩等，逐字段定义 |
| pose / twist | 空间位姿／线速度与角速度 |
| wrench | 力和力矩，分别声明 N、N*m |
| tactile / contact | 触觉阵列或特征／接触状态和事件 |
| point_cloud / range_scan | 点云／距离扫描 |
| event_camera | 事件相机事件流 |
| audio | 音频 |
| odometry / gnss | 里程计／卫星定位 |
| system_status | 电池、温度、故障状态等遥测 |
| task_text / annotation | 任务文本／标签和标注载荷 |
| other | 未分类 |

可用 team__custom_name 扩展模态，不要求修改核心词表。模态清单与解码支持清单分开：
声明一个模态不使 value_available/image_available 自动变为 true。新消息编码仍需 reader 提供。
结构化 JSON/Parquet 的已声明数值字段支持数值/张量读取，HDF5 显式深度/触觉等张量字段
保留原维度；压缩深度、变长点云、事件流、音频格式尚未完成通用解码验收。
read_signal 面向可转数值数组的字段；文本/记录使用 reader 支持的 read_values 路径，不冒充数值。

兼容：旧 image → rgb，joint_state/cmd → joint_space；旧 cmd 保留 command 来源，
除非配置显式给出 source_kind。Channel.modality 对外输出规范名称。
旧算法的 DataInfo/algorithm_view 继续转换为旧模态名，保证旧算法入口兼容。
旧 image 别名只是历史名称兼容，不代表验证了图像确实是彩色。
原先依据 Channel.modality == 'joint_state' 或 'image' 编写的 alpha 插件需改为新名称。

## Dataset 管任务定义，Episode 管关联与标注

完整 dataset.yaml 示例：

```yaml
schema_version: 1
id: tea_dataset
version: '1'
metadata:
  description: 桌面倒茶示教
  collection_site: lab_a
  custom:
    batch: batch_001

tasks:
  - id: pour_tea
    name: 倒茶
    description: 拿起茶壶，向杯中倒茶后放回。
    subtasks:
      - id: grasp
        name: 抓取茶壶
        description: 接近把手并握住茶壶。
      - id: pour
        name: 倒入茶水
        description: 将壶口移到杯口上方并倾斜。

episodes:
  - id: episode_001
    path: episodes/episode_001
    format: episode
    metadata:
      task_ids: [pour_tea]
      instruction: 给面前的杯子倒茶。
      custom: {demonstrator_code: anonymous_01}
      subtask_annotations:
        - task_id: pour_tea
          subtask_id: grasp
          source: human
          span: {unit: frame, channel: head_rgb, start: 20, end: 81}
        - task_id: pour_tea
          subtask_id: pour
          source: human
          span:
            unit: s
            start: 3.5
            end: 6.0
            clock: robot_clock
            time_source: acquisition
```

跨度统一为半开区间 [start,end)：上例帧 20 到 80。frame 必须写 channel，秒必须写
clock/time_source；不允许省略时间基准后按视频 FPS 猜测。span 可省略，表示未定位的子任务标注。
任务 ID 在数据集内唯一；子任务 ID 在所属任务内唯一。Episode 可关联多个任务，也可没有任务。
框架检查引用存在、区间类型/顺序，不强制子任务顺序、覆盖率或不重叠。
这些是标注信息，不自动证明时钟正确、帧区间在真实通道内、动作完成或模型判断准确。
标注与真实通道范围的一致性验收仍需后续检查器。

旧 tasks: [倒茶, 抓取] 仍可用；结构化定义适合跨算法复用。非标准字段存 metadata/custom，
保留来源、版本、采集条件和其它附加信息；不要把未知业务标注转成自动筛选结论。
本版支持 dataset.yaml 显式元数据；尚未自动将所有格式的原生任务标注转换到该目录。

```python
from edc import open_dataset
with open_dataset('/path/to/dataset') as dataset:
    print(dataset.tasks)                     # 原任务定义
    print(dataset.task_catalog['pour_tea'])  # 按 ID 查询
    print(dataset.metadata)                  # 数据集附加信息
    for episode in dataset.iter_episodes():
        print(episode.metadata.get('task_ids', []))
        print(episode.metadata.get('subtask_annotations', []))
```

inspect 输出和 run.json 输入快照均保留任务定义、Dataset metadata、Episode metadata。
插件通过 context.dataset/context.episode 读取，不要求当前 CPU 算法使用它们。
