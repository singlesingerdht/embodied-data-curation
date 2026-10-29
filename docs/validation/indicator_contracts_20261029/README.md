# 指标池配置与执行契约验收

本轮完善 57 项指标的输入声明、逻辑名绑定、可选依赖和推荐到执行的适配。
检测方法、评分公式与物理阈值未改动；对照提交为 `6c8d085`，110 个底层计算 Python
文件逐字节一致。指标适配调用修复不等同于新增检测方法。

## 接口变化

| 端 | 当前规则 |
| --- | --- |
| 使用者配置 | `channels` 可选择多个逻辑通道；不填时由指标预检确定默认模态范围，记录实际来源 |
| 使用者配置 | `required_channels`、`field_map` 的键也解析逻辑名；不同引用指向同一来源时拒绝重复绑定 |
| 算法开发者 | `data_requirements.channel_param` 支持字符串或字符串数组，逐通道检查；`min_channels` 声明最低数量 |
| 算法开发者 | 未填写可选数组时显示 `deferred`，必须有算法预检处理默认范围；未填写普通必需通道仍为缺输入 |
| 推荐输出 | 展示生效参数、逐通道需求检查、实际输入绑定和缺依赖等原因；共同 clock 仍由跨通道预检确认 |
| 执行输出 | 沿用 `dqs_record_v1`，保存原方法明细及 `indicator-raw.json`、`source-mapping.json`、`provenance.json` |

55 项通过 `data_requirements` 声明通道输入；M8 声明 `dataset_file`，M9 声明 `tf_chain`。
覆盖率 57/57 表示登记契约覆盖，不表示 57 项全部实现或全部可运行。现有 11 项未实现，
以及双时钟、编码头、格式语义等尚未接通的输入边界，继续明确阻塞。

图像内容指标声明 `cv2/xxhash/scipy`，位姿内容及同步指标声明 `scipy`。
这些仍是可选执行依赖，基础安装只需 NumPy/PyYAML；登记发现不加载这些库或 GPU 模型。

## 配置例子

以下为合成验收样例中的参数，不是给真实机器人确认单位或标定：

```json
{
  "J5": {
    "channels": ["joint/arm/left/feedback", "joint/arm/right/feedback"]
  },
  "A5": {
    "channels": ["joint/arm/left/command"],
    "field_map": {
      "joint/arm/left/command": {"joint_q": "position"}
    }
  }
}
```

机器人配置分别把以上逻辑名绑定到 `left/right/command` 三个源字段，指令和反馈保持独立。
同一份参数文件可用于 `edc recommend` 和所选算法的 `edc run`。

已验证的例子：

- J1/J4/J5 默认选择双臂反馈与显式选择相同两路时，原始检测明细和指标输出一致。
- A5 通过逻辑名把 `joint_q` 映射到指令通道的 `position`；源值为 `90 deg`，算法实际读取
  `π/2 rad`，未读取反馈通道，源文件哈希不变。
- M1 的两路数据各有采集时间、且 clock 相同可进入执行；不同 clock 或使用容器时间会缺输入。
- M3 的必需通道不能与显式选择的检查范围冲突；M8 不要求无关传感器通道。
- 缺运行依赖、字段不存在、指令/反馈不符、逻辑名重复及缺默认范围预检，均在输入侧给出原因。

指标结果中 `score/passed/verdict` 为 `null`，`assessment_complete=false`。
`status=completed` 表示本次指标证据生成完成；质量放行仍需独立判断，不能把未评估项当作通过。

## 验证依据与复现

测试及安装结果见 [validation.json](validation.json)、[源码回归](source-tests.log)
和 [安装包回归](wheel-tests.log)。均为合成契约验证，本轮没有重跑真实机器人全部指标，
没有重新验收官方 LeRobot/GPU 环境。

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/edc/test_indicator_contracts.py \
  tests/edc/test_algorithm_contract_pool.py \
  tests/edc/test_plugin_refinements.py -q -rs
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/edc -q -rs
python -m build
```

新增测试覆盖真实适配调用签名，避免仅验证元数据存在而遗漏“推荐可用、实际调用失败”。
CI 同时检查安装 wheel 后 57 项指标的结构化输入声明是否齐全。
