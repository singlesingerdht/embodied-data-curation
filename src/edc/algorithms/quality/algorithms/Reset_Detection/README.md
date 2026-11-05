# 复位检测（Reset_Detection）

看一条数据的**开头和结尾**，手臂是不是回到了采集团队约定的 home 姿态。

## 怎么配

在机器人 yaml 的每条手臂下写 `home`，二选一，所有手臂写法和容差要一致：

```yaml
arms:
  left:
    home:
      joints: [-0.19, 0.45, -0.06, -1.67, -0.04, 0.00, 0.14]   # 关节角，rad，个数等于关节数
      tolerance: 0.6                                          # 容差，rad
    # 或者 eef_position: [x, y, z]（末端位置，m，需要 FK 或记录的末端位姿）
```

给不出确认过的 home，或者数据是从长录像里剪出来的片段（开头结尾本来就不在 home），就在 yaml 写 `checks.skip: [Reset_Detection]` 跳过。

## 怎么判断（`method.py`）

- 每条手臂取第一帧和最后一帧，和 home 逐个关节（或 x/y/z）求偏差。
- **yaml 卡片写法（v1）**：开头、结尾的最大偏差都不超过 `tolerance` → 通过；否则复核。报告写出偏差最大的关节、偏了多少、在第几秒。
- **完整 profile 写法（v2）**：逐通道给 `home`、`tolerance1`、`tolerance2`。偏差 ≤ tolerance1 得 100 分，≥ tolerance2 得 0 分，中间线性；任一分量 0 分则整条手臂 0 分；开头、结尾取较低分；0 分复核。

## 文件

- `algorithm.py`：把 yaml 的 home 换成国际单位、检查个数和单位对不对（不对就记“需要标定”，不运行）。
- `method.py`：上面的判断。
- `result.py`：写成报告里的句子。
