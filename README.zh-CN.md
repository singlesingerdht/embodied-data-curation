# 具身数据质量检测（EDC）

把一批机器人数据交给框架，框架用 9 个质量算法逐条检查，出一份 HTML 报告：每条数据是**通过、复核、可救还是排除**，以及原因。

**接入一个新机型，只需要写一个 yaml。** yaml 里说清楚数据里哪一列是关节、哪一列是末端、单位是什么、相机有哪几路、URDF 在哪；其余的事（关节限位、末端位置、各算法该读哪些数据）框架自动完成。LeRobot v3.0 优先，也支持 LeRobot v2.1、HDF5、Parquet、ROS1/ROS2 bag、MCAP、单条 JSON+MP4 目录。

[English](README.md)

---

## 快速开始（服务器）

服务器：`ssh -p 40112 root@113.56.214.18`，框架目录 `/mnt/public/rxs/code/embodied-data-curation`，环境已装好。

```bash
cd /mnt/public/rxs/code/embodied-data-curation
.venv/bin/edc batch /mnt/public/rxs/data/Lerobotv30/pepsi_sort_8ep --robot configs/robots/pepsi_sort.yaml --output reports/pepsi.html
```

跑完把 `reports/pepsi.html` 下载到本地，用浏览器打开（同名 `.json` 是同样的结论，给程序用）。

已经写好的机器人 yaml（都在 `configs/robots/`）：

| 名字 | 机型 | 数据格式 | 说明 |
|---|---|---|---|
| `x1pro` | 自变量 Quanta X1 Pro，双臂 6 关节 | 单条目录：JSON 轨迹 + 3 路 MP4 | 有厂家 URDF；`/mnt/public/datasets/x1pro` |
| `leju_kuavo` | 乐聚 KUAVO 4pro，双臂 7 关节 + 灵巧手 + IMU | ROS1 bag | 有 URDF；末端位置由 URDF 算 |
| `pepsi_sort` | 单臂夹爪，只有末端位姿 | LeRobot v3.0，60 Hz | 测试数据 `/mnt/public/rxs/data/Lerobotv30/pepsi_sort_8ep` |
| `luoshi` | 珞石/开阳双臂 7 关节 | LeRobot v3.0，图片列 | 测试数据 `/mnt/public/rxs/data/Lerobotv30/luoshi_1ep` |

---

## 接入一个新机型（从写 yaml 开始）

### 第 1 步：看数据里有什么

```bash
.venv/bin/edc channels <一条数据或数据集目录>
```

列出数据里每一项的**名字**（下面 yaml 里的 `source` 就填它）、类型（数值 / 图像）、行数、每行几个数。例如 LeRobot：

```
observation.state        数值   453  每行 (8,)
observation.images.main  图像   453  (video/mp4)
```

### 第 2 步：复制模板，填 yaml

模板在 `configs/templates/`：

| 模板 | 适用 |
|---|---|
| `single_arm.yaml` | 单臂（可以只有关节、只有末端，或两者都有） |
| `dual_arm.yaml` | 双臂（**字段说明最全，先看这份**） |
| `dual_arm_hand.yaml` | 双臂 + 灵巧手（ROS 消息写法示例） |

复制成 `configs/robots/<机器人名>.yaml`，按注释填。核心写法只有一种：

```yaml
joints: {source: observation.state, index: 0-6, unit: rad}
#        数据里的名字              第几个数      单位
```

- `source`：第 1 步列出的名字。
- `field`：只有 ROS 消息要写（如 `/joint_states` 的 `position`）；表格数据（LeRobot、Parquet、HDF5、JSON）不写。
- `index`：一个数 `7`、一段 `0-6`（含两端）或列表 `[0, 2, 4]`；整列就是这一项时不写。
- `unit`：**每一项都必填**，写清楚这列数是什么单位：
  - 关节 `rad`/`deg`，关节速度 `rad/s`/`deg/s`，末端位置 `m`/`cm`/`mm`；
  - 末端姿态 `quaternion`（再写 `order: xyzw` 或 `wxyz`）、`rpy_rad`/`rpy_deg`（URDF 的 roll pitch yaw）；
  - 夹爪 `m`/`cm`/`mm`（开口）或 `rad`/`deg`（转角）；灵巧手 `rad`/`deg`/`m`/`mm`；`other` 还可以写 `m/s`、`N*m` 等；
  - `raw`：没有物理单位的原始数值（如 0~255 的夹爪读数、0~100 的灵巧手读数），**要同时写 `range: [最小, 最大]`**，超出范围判排除；
  - `unknown`：确实不清楚，只查有没有 NaN/无穷大。查清以后改掉。
- `range`：选填（夹爪、灵巧手、末端姿态以外的 `other` 可写）：正常范围 `[最小, 最大]`，超出判排除。
- `scale`：数据存的是原始数值、能线性换算成物理单位时写，两点定一条直线。比如关节存的是 12 位编码器读数 0~4095，对应 -π~π：

```yaml
joints: {source: observation.state, index: 0-5, unit: rad, scale: {raw: [0, 4095], value: [-3.1416, 3.1416]}}
```

yaml 各部分：

| 部分 | 必填？ | 内容 |
|---|---|---|
| `robot` | 必填 | `name`（英文，和文件名一致）、`model`、`confirmed_by`（确认单位和含义的人；改了文件就等于此人重新确认） |
| `data` | 选填 | `time`：原始采集时间列（见下文“时间”）；`time_note`：没有原始采集时间时写一句原因，显示在时间类检查“不适用”的格子里；`min_duration_s`：最短时长，默认 3 秒；`video_frame_time: rows`：单条目录里第 i 帧视频对应第 i 行 |
| `arms` | 至少一条 | 每条手臂：`joints` 关节、`joint_velocity` 关节速度、`joint_command` 关节指令、`eef_position` 末端位置（或 `fk`：用 URDF 算）、`eef_orientation` 末端姿态、`eef_command` 末端指令、`gripper`/`gripper_command` 夹爪、`hand`/`hand_command` 灵巧手、`urdf`、`home` 复位位置、`max_joint_velocity` 速度上限。双臂用 `left`/`right` 命名，单臂随便起名 |
| `cameras` | 建议 | 每路相机：`source`、`mount`（head / chest / wrist / main）、手腕相机写 `arm`、深度图写 `type: depth` |
| `imu` | 选填 | IMU 的角速度、加速度、姿态字段 |
| `other` | 选填 | 其他数值（腰、头、底盘、力矩……），查有没有 NaN/无穷大，写了 `range` 再查范围 |
| `checks` | 选填 | 算法参数，只写和默认值不同的（见“算法参数”） |

几条规则：
- **没写进 yaml 的数据不检查。**
- 夹爪、灵巧手、末端姿态、`other` 没有专门的物理检查：查有没有 NaN/无穷大，写了 `range` 的再查范围。末端姿态的格式写清楚后，第 4 步会用它验算 URDF。
- 写了 `joint_velocity` 就要有速度上限：URDF 里有 `<limit velocity>`，或者写 `max_joint_velocity`。
- 两条手臂的 `home` 要用同一种写法（都按关节角或都按末端位置）和同一个 `tolerance`。

### 第 3 步：核对 yaml 和数据对不对得上

```bash
.venv/bin/edc robot check --robot configs/robots/<机器人名>.yaml --data <一条数据>
```

逐项读一遍 yaml 里写的数据，打印每一项的行数、个数和数值范围（写了 `range` 的，超出的打 ✗）。有 ✗ 的行按提示改。全是 ✓ 之后，**再人工看一眼数值范围是否符合单位**（关节角一般在 ±3.2 rad 内，末端位置一般在 ±2 m 内）：比如把 deg 写成了 rad，数会大几十倍。

### 第 4 步（有 URDF 时）：把 URDF 写进 yaml，再用数据核对

URDF 的写法全部由你在 yaml 里说清楚，程序不猜。分三小步。

**4.1 查名字**：列出 URDF 的连杆树（每个关节的名字、类型、限位、速度上限）。

```bash
.venv/bin/edc robot urdf --urdf <URDF 文件>
```

从树里选两个连杆：
- `base_link` 起点：手臂装在身上的那个连杆（数据里录了末端位置时，最好是末端位置所用的坐标系；不是也行，核对时会自动对齐坐标系，并打印数据坐标系相对它的平移和旋转）。
- `tip_link` 末端：手的工作点，名字常含 `end_effector`、`tool`、`tcp`、`ee`。

再运行下面的命令，列出两者之间的可动关节，并打印可以照抄的几行：

```bash
.venv/bin/edc robot urdf --urdf <URDF 文件> --base <起点连杆> --tip <末端连杆>
```

**4.2 写进 yaml**（每条手臂一段）：

```yaml
    urdf:
      file: x1pro/x1pro_left_arm.urdf        # 相对 yaml 所在目录
      joints: [left_arm_joint1, left_arm_joint2, left_arm_joint3, left_arm_joint4, left_arm_joint5, left_arm_joint6]
      base_link: left_arm_base_link
      tip_link: left_arm_link6
      # tip_offset: [0, 0, 0.1]              # 选填：数据记录的末端点在 tip_link 坐标系里的位置（m）
```

- `joints`：**按数据的列顺序**写：`joints.index` 选出来的第 1 列是 URDF 里哪个关节，就写在第 1 个，依此类推。个数要和 `joints.index` 一样，并且正好是 base_link 到 tip_link 之间的全部可动关节。
- 数据里的关节角要和 URDF 是同一套方向和零位（多数厂家的数据就是按 URDF 发的）。不是同一套时核对会对不上，这份 URDF 不能直接用。
- `tip_offset`：数据记录的“末端”不在 tip_link 原点、而在它前方固定的一点时写（比如两指中间比夹爪法兰往前 10 cm）。它不是 URDF 的内容，是这份数据把哪一点叫末端；同一台机器换个采集程序可能不同。这个数要从数据的说明里查（仿真器、SDK 文档里“末端”的定义），核对不会替你算。写了以后 `eef_position: fk` 算的也是这一点。
- 写错时 yaml 报错会说清楚，比如“没写 left_arm_joint6；这条链上没有 left_arm_joint7”，并列出 URDF 里的正确顺序。

**4.3 用数据核对**：

```bash
.venv/bin/edc robot urdf --robot configs/robots/<机器人名>.yaml --data <数据1> <数据2>
```

要**两条**数据（一个文件里有多条数据时，如 robomimic 的 hdf5，写一个文件就够，自动取前两条）。

每条手臂打印：
1. 数据第几列对应 URDF 哪个关节、这列数据的范围和 URDF 限位（已放宽 0.01 rad）。超出限位的行超过 1% 打 ✗。
2. 数据录了末端位置的：用 URDF 从关节角算出末端（tip_link 原点，写了 `tip_offset` 就是那一点），和记录值比。数据记录末端用的坐标系 URDF 里没有（比如仿真的世界坐标系），所以用**第一条**数据求出它相对 base_link 的平移和旋转（打印出来，如 robomimic 的 Franka 底座在 (-0.56, 0, 0.91) m、旋转 0°），再**原样**用到**第二条**数据上比。第二条没参与求坐标系，它也对得上才算数。写了 `eef_orientation`（四元数或 rpy）的，姿态也一起比（末端坐标轴的朝向约定也从第一条求）。
3. 结论，三种之一（只判对错，不给改法）：
   - **对得上**：两条数据的位置误差都在 10 mm 以内，姿态误差都在 5° 以内，关节都在限位内。
   - **对不上**：有关节超过 1% 的行超出限位，或末端差得超过标准。对照 yaml 逐项查：单位、`joints` 顺序、`tip_offset`、base_link/tip_link、URDF 版本，以及数据的关节方向和零位是不是和 URDF 一样。
   - **末端未验证**：数据没录末端位置（比如乐聚），或只有一条可用的数据，只查了关节范围。

用 X1 Pro 真实数据试过：yaml 写对时，第二条数据左臂 0.6 mm / 0.4°、右臂 1.6 mm / 0.8°；故意写错关节顺序、单位、姿态格式、tip_offset，都判对不上。

注意：
- **只收普通 URDF。** 厂家给的是 xacro（含 `${...}`）时，先展开：`pip install xacro` 后运行 `xacro 文件.urdf.xacro > 文件.urdf`。只有宏定义、没有调用的 xacro（如 X1 Pro）要写几行调用，参考 `input_example/tools/x1pro_expand_xacro.py`。
- URDF 里手臂用不到的部分（手指的 mimic 关节、速度写 0 等）不影响，只在 4.1 里提示。
- 数据里没录末端位置时，yaml 里可以写 `eef_position: fk`，让框架用 URDF 算末端。
- 核对只能发现数据里“动起来能看出来”的错。数据坐标系是求出来的，所以和“整个坐标系挪一下”效果一样的错误看不出来：X1 Pro 最靠近底座那节连杆改长 4 cm 只差 2.9 mm（其余几节 15~21 mm，判对不上）；robomimic lift 里手一直朝下，Franka 不写 9.65 cm 的 tip_offset 也只差 7 mm。所以动作越丰富（手的朝向变化越大）的两条数据，核对越可靠。

URDF 写进 yaml 后，框架自动从 URDF 读关节限位（上下各放宽 0.01 rad，可用 `limit_margin` 改）和速度上限交给数值检查，需要时用它算末端位置。

### 第 5 步：批量质检

```bash
.venv/bin/edc batch <数据目录或文件，可以多个> --robot configs/robots/<机器人名>.yaml --output reports/<名字>.html
```

- 输入可以是目录（递归查找）或单个文件。LeRobot 数据集、多条 HDF5 会自动按条拆开。
- 常用选项：`--jobs N` 同时跑几条（默认 16，服务器共用）；`--tmp <目录>` 临时文件放哪（数据大时放到大盘，如 `/mnt/public/rxs/code/edc_tmp`）；`--keep <新目录>` 保留每条的完整输出（含 `quality.json` 和证据图）。

---

## 报告怎么读

### 每个检查项的结论

每个算法对每一项（一条手臂、一路相机、一个字段）给出下面之一：

| 结论 | 意思 |
|---|---|
| **通过** | 没有问题 |
| **复核** | 发现可疑之处，也可能是正常情况，需要人看 |
| **可救** | 有问题，但原因清楚、能修好；格子里写了怎么修 |
| **排除** | 这一项的数据不能用 |
| 不适用 | 这条数据没有这项可查，原因写在格子里（例如没有原始采集时间、yaml 没写复位位置、数据太短） |
| 没跑完 | 算法出错，原因写在格子里 |

表格每格显示该算法最差的一项，鼠标停在格子上看原因。

### 整条数据的结论

按顺序判断，先满足哪条就是哪条：

1. 有任何一项“排除” → **含建议排除项**
2. 有“可救” → **可救**
3. 有算法没跑完 → **未评估完**
4. 有“复核” → **建议复核**
5. 其余 → **建议保留**

“不适用”不影响结论。程序本身没跑完的条目记为 **运行失败**，详情里有日志。

### 9 个算法

| 算法 | 查什么 | 怎么判 | 什么时候不适用 |
|---|---|---|---|
| 结构完整性 | 相机、通道是否齐全；时长 | 缺东西或短于 `min_duration_s` → 排除（太短时其余 8 项不检查） | — |
| 数值检查 | NaN/无穷大；关节超 URDF 限位；关节速度超上限；动过之后长时间不变（冻结）；关节读数卡住 | NaN、超限 → 排除；冻结 → 复核；整条手臂关节卡住而末端在动 → 可救（用逆运动学由末端位姿反算关节） | — |
| 动作平滑度 | 手停住时末端在不在抖：轨迹里每秒来回 2–10 次的部分（更慢的是有意动作）的加速度；另查运动中位置读数突然完全不变（冻结） | 只有通过、复核、不适用，不排除：停住时超过 1.0 米/秒² → 复核（暂定）；冻结 → 复核。运动时的数值只列出来参考 | 没有末端位置；采样率低于 10 Hz；手没停住过 |
| 时间戳对齐 | 各路时间间隔是否稳定、能否配对；共用一条时间时只查跳帧 | 按分数通过 / 复核 / 排除；共用时间时，超过中位间隔 3 倍的空档 → 复核 | 没有原始采集时间 |
| 采集同步 | 两组设备时钟是否对得上 | 固定偏差 → 可救（程序已平移）；偏差在变 → 排除 | 没有各通道各自的原始时间（LeRobot、JSON 等共用一条时间的格式） |
| 时间异常归因 | 时间出问题是哪一路造成的 | 多路同时断档等 → 复核 | 同上 |
| 时间／序列重复 | 重复的时间戳或序号 | 按重复比例 | 没有原始时间值 |
| 复位检测 | 开头、结尾是否在 `home` 附近 | 超出 `tolerance` → 复核 | yaml 没写 `home`；从长录制剪出的切片 |
| 视频完整性 | 黑屏、花屏、模糊、遮挡、冻结等 | 每 5 帧抽 1 帧，某类问题占 30% 及以上 → 排除，否则通过并注明问题帧数 | 没有相机 |

### 时间

- **ROS bag / MCAP**：每个话题自带采集时间，时间类 4 个算法都能查。
- **LeRobot**：标准的 `timestamp` 列是“帧号 ÷ 帧率”算出来的，不是采集时间，所以时间戳对齐、采集同步、时间异常归因、重复检查记为“不适用”。在 yaml 写 `data.time_note: 一句原因`，报告里这几格就显示这句原因（例：pepsi_sort.yaml）。数据里如果另存了原始采集时间列，写 `data.time: {source: 列名, unit: s/ms/us/ns}`，时间戳对齐和重复检查就会按真实时间做（例：luoshi.yaml）。
- **单条 JSON+MP4、HDF5、Parquet**：有时间列就写 `data.time`。

---

## 算法参数（yaml 的 `checks` 部分，只写要改的）

| 算法 | 参数 | 默认 | 含义 |
|---|---|---|---|
| Numeric_Check | `freeze_min_duration_s` | 5 | 数值连续不变超过几秒才算冻结 |
| Numeric_Check | `joint_stuck` | 不开 | `{min_move_m: 0.02, min_moving_s: 1.0, moving_speed_m_s: 0.02, verdict: DO_NOT_USE}`：整条手臂关节读数不变、末端却在动时判可救（需要同时有关节和末端） |
| Action_Smoothness | `still_review_m_s2` | 1.0 | 停住时抖动加速度的复核线（米/秒²） |
| Action_Smoothness | `band_hz` | [2, 10] | 算作抖动的运动频率（每秒来回几次）；上限最多取采样率的 0.4 倍 |
| Action_Smoothness | `min_rate_hz` | 10 | 采样率低于它不检查 |
| Action_Smoothness | `still_speed_m_s` / `still_min_s` | 0.01 / 0.3 | 有意动作慢于 1 厘米/秒、持续 0.3 秒以上算停住 |
| Check_Timestamp | `shared_row_max_gap_s` | 中位行间隔 × 3 | 共用一条时间时，相邻两行最多隔多少秒 |
| Check_Timestamp | `scoring` | 算法默认 | 评分参数（X1 Pro、乐聚的取值见它们的 yaml） |
| Reset_Detection | `split_segments` | false | 数据集带剪辑记录（`anno/source_segment.json` 或上级 `split_manifest.json`）时，切片不查复位 |
| Root_Cause | `families` | 算法默认 | 哪些通道属于同一台设备 |
| Video_Integrity | `sample_step` | 5 | 每几帧抽 1 帧 |

---

## 已知问题

- **动作平滑度的阈值是暂定值**（2026-11-01 重写算法）：由 X1 Pro、乐聚、pepsi_sort 的实际分布和注入测试定出，还没有用人工标注校准。采样率 15–25 Hz 的数据只查得到采样率 0.4 倍以下的抖动（15 Hz → 6 Hz 以下）。**运动中的抖动目前不判断**：快速伸手、急停、撒盐这类正常动作本身也落在 2–10 Hz，和抖动分不开（2026-11-01 看 X1 Pro 撒辣椒、分发玩具两条视频确认），只列数值参考。
- **LeRobot 没有原始采集时间**时，4 个时间类算法都不适用。pepsi_sort 的 `source_timestamp` 存成了 float32，Unix 秒精度只剩约 128 秒，不能用。
- **URDF 核对**只能抓出明显不对的 URDF 和写错的 yaml，抓不出几毫米的小偏差，见第 4 步。数据没录末端位置时（如乐聚）只能查关节范围，末端未验证。
- 灵巧手、夹爪、末端姿态没有物理检查：只查有没有坏数，写了 `range` 的再查范围。

---

## 框架结构

```
configs/robots/<机器人>.yaml        用户写的机器人 yaml（唯一需要写的文件）+ URDF
configs/templates/                  模板：单臂 / 双臂 / 双臂灵巧手
src/edc/
  execution/cli.py                  命令行：channels、robot check、robot urdf、batch、run、algorithms
  execution/batch.py                批量：找数据、按条并行、汇总
  execution/runner.py               单条：读数据 → 跑算法 → 写 records.jsonl 和 quality.json
  robots/card/                      把机器人 yaml 展开成框架内部格式（用户看不到这一步）：按 yaml 的部分分文件，
                                    arms.py 手臂、urdf.py 手臂的 urdf、sensors.py 相机/IMU/other、__init__.py 总流程
  robots/yaml_check.py, urdf_check.py   yaml 核对、URDF 查名字和核对
  data/readers/                     各格式读取器（按格式，不按机型）
  data/transforms/                  urdf.py 读 URDF（全项目唯一读 URDF 的地方）、core.py 正运动学、重采样
  algorithms/base.py                统一结果格式与新算法基类（通过 / 复核 / 可救 / 排除）
  algorithms/quality/algorithms/<算法>/
      run_check.py 等               算法本体（计算）
      result.py                     本算法的结论和原因怎么写、报告里“怎么复核”的说明
  algorithms/quality/shared/verdicts.py   9 个算法共用的结论规则（哪个记录结论算哪一类、太短、时钟可救、怎么修）
  algorithms/quality/adapters/      把 yaml 里的数据和参数交给算法（cpu.py）；results.py 收集结果、写 quality.json
  reporting/batch.py                HTML 报告（只显示统一结果，不做判断）
examples/plugins/algorithm/joint_jump.py   开发者示例：按统一基类写的新算法
examples/plugins/reader/            开发者示例：接入一种新数据格式
tests/                              测试（tests/edc/fixtures 是测试用的配置）
```

开发者接入新算法：继承 `edc.algorithms.base.QualityAlgorithm`，实现 `check(episode, params)`，返回 `Item` 列表（每项一个结论和原因；除“通过”外，复核、可救、排除、不适用、没跑完都必须写 `reason`，不写会直接报错），用 `edc.algorithms` 入口注册。示例见 `examples/plugins/algorithm/joint_jump.py`，装上后 `edc batch` 会自动多出一列。

## 测试

```bash
.venv/bin/python -m pytest -q tests
```
