# 首帧摆放检查

`Initial_Placement_Assessment` 读取每条轨迹指定 RGB 通道的第 0 帧，按采集 SOP 中的初始摆放要求逐项判断。例如，“文具盒在可见台面的左侧”需要图像中可辨认的文具盒和台面方位。精确物理距离、三维关系或无法辨认的目标可以返回“无法判断”。跨轨迹的分布、网格、熵和热力图由独立的 `Initial_State_Distribution` 计算。

## 准备和调用

1. 从 EDC 源码目录执行 `python -m pip install '.[placement,formats]'`。
2. 默认模型为 `qwen3.8-max`。示例使用阿里云百炼北京地域的 OpenAI 兼容接口。请在运行 EDC 的终端设置环境变量 `DASHSCOPE_API_KEY`，使用开通了该模型的账号和与端点地域匹配的密钥。[获取 API Key](https://help.aliyun.com/zh/model-studio/get-api-key/)
3. 复制机器人配置并填写相机源和摆放要求。示例中的 `allow_remote_upload: true` 允许将所选首帧和任务文本发送给配置的服务，`enable_thinking: false` 沿用原方法的配置。若使用自己的大模型网关，可设置 `base_url`、`api_key_env` 和该服务提供的 `model`。服务须支持图片输入及 JSON 输出。[机器人配置](../../../../../../configs/robots/placement_example.yaml)
4. 运行：

~~~bash
edc run /path/to/dataset --robot /path/to/robot.yaml \
  --algorithm Initial_Placement_Assessment --output /path/to/results
~~~

不同轨迹采用不同 SOP 时，在数据集的 `dataset.yaml` 中填写各轨迹的 `metadata.task_requirements`，其优先级高于机器人配置中同名参数。可选的 `task_description` 仅补充任务背景。示例使用已有 RGB 通道的 HDF5 轨迹：

~~~yaml
schema_version: 1
id: pencil_task
episodes:
  - id: recording_001
    path: recording_001.h5
    format: hdf5
    metadata:
      task_requirements: 文具盒在可见台面的左侧。
      task_description: 将笔放入文具盒。
~~~

图片和视频均通过 EDC 的 RGB 通道读取。可以将原初始分布方法的配置合并到同一份机器人配置的 `checks` 下，并在命令中追加 `--algorithm Initial_State_Distribution`。分布检查需额外准备 SAM3、目标类别和可比较的相机／场景分组。

## 输出

`catalog.json` 索引每条轨迹的 `edc_record_v1` 记录及证据。原生报告 `placement_report.json` 保留逐项规则原文、观察、理由、`placement_status`、`passed`、`requirements_complete`、未检查要求和限制。发现违反项时整体为 `incorrect`，即使其他项未完成。全部要求确认满足时为 `correct`，其余为 `undetermined`。`assessment_complete` 表示摆放要求是否评估完整。方法没有数值分数。

模型调用前保存来源、首帧及输入映射。正常返回的原始模型 JSON 保存在 `provider-response.json`。模型响应无法解析或引用了输入中不存在的规则时，EDC 记录处理失败并保留已有证据。模型服务提供的模型名和本地方法指纹见 `provenance.json`。权重版本由服务部署方管理。来源和许可状态见 [SOURCE.json](SOURCE.json)。
