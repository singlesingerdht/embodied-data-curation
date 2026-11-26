# 人手干预检测（Hand_Intervention）

[English](README.md)

检查多路相机里有没有人的手／手臂新进入任务区，或碰到、移动任务物体（对应数据质检 SOW 的 S03）。方法取自原四事件安全检测（Collision_Detection_Qwen3vl v62）的人手部分，去掉了碰撞、洒漏、盖子跌落和几何输入，所以不需要深度、内参和手臂标定。

## 方法

1. **扫描**：每 `window_seconds`（默认 12）秒一个窗口，相邻窗口重叠 2 秒。每路相机按 `scan_fps`（默认 2）抽帧拼成一张带时间的拼图，连同任务说明和上一窗口末的人手状态一起问本地 Qwen3-VL，宁多勿漏地列出候选。模型自己写明「只在背景、没有接触」的候选去掉。
2. **合并**：间隔 2 秒以内的候选并成一段，合并后最长 14 秒。
3. **复核**：每段前后各加 1 秒，按 `review_fps`（默认 4）抽更密的帧再问一次：确认／需复核／排除。确认必须写出看得见的人手接触或移动物体，只看到进入、靠近的降为需复核；没写出具体画面证据的也降为需复核。
4. **整条结论**：有确认判 FAIL；只有需复核，或某个窗口模型没答出有效结果，判 NEEDS_REVIEW；否则 PASS。

配置的相机这条数据里没有时跳过，用其余相机检查，并在 `reason` 和 `missing_views` 里写明。

## 运行

```bash
edc run /path/to/episode.mcap --robot configs/robots/x1pro_mcap.yaml --algorithm Hand_Intervention --output /path/to/results
```

机器人 yaml 的 `checks.Hand_Intervention`：

| 参数 | 说明 |
|---|---|
| `views` | `{视角名: 相机}`，视角名会告诉模型，如 `{head: rgb/head, chest: rgb/chest, left_wrist: rgb/wrist/left, right_wrist: rgb/wrist/right}` |
| `model` | Qwen3-VL 模型目录；和首帧摆放共用，同一次运行只加载一次 |
| `task` | 任务说明；不填读源 MCAP 的 `task_topic`（默认 `/instruction_data`） |
| `window_seconds`、`scan_fps`、`review_fps` | 见上 |

结果在 `result.json`：`verdict`、`reason`、`events`（每段的起止秒、确认／需复核／排除、涉及物体、证据描述、扫描候选）和 `scan_failures`。没排除的事件每路相机存一张复核拼图 `eventNN_K.jpg`，方便人看。

## 江西数据验证（2026-11-26，269 条，Qwen3-VL-4B）

| | 条数 | FAIL | NEEDS_REVIEW | PASS |
|---|---|---|---|---|
| 人工标了 S03 | 10 | 8 | 1 | 1 |
| 人工没标 | 259 | 2 | 1 | 256 |

- 人工标了的 10 条查出 9 条；漏掉的是 2026_09_22_14_56_04（炒菜任务）。
- 人工没标但报了的 3 条：
  - 2026_08_31_09_19_35 搅辣椒面：70~72.5 秒真人拿走勺子，疑似人工漏标；
  - 2026_08_31_09_12_30 舀绿豆：腕部相机里背景有人走过、没碰物体，误报；
  - 2026_08_28_09_05_23 封食品袋：需复核，未核对。
- 三项（加 S08、S11）一起跑平均每条约 2 分钟（含读文件、解码、加载模型）；A100 上 4B 模型约占 5 GB 显存。

## 局限

- 判断全靠 4B 视觉语言模型，背景里走过的人可能被当成干预。
- 标注样本只有 10 条，准确率还需要更多数据确认。
