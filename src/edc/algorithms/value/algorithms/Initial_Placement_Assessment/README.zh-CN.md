# 首帧摆放检查（Initial_Placement_Assessment）

[English](README.md)

检查首帧里 SOP 写的物体是否放在 SOP 指定的桌面九宫格（对应数据质检 SOW 的 S08）。

## 方法

1. **物体名**：SOP 字段名（如「装有水的杯子在操作面位置」）翻成英文类别名，并标出扁平／大件物体。先查同目录的 `object_names.json`（可人工修改），新字段名才问本地 Qwen3-VL，答上的写回该文件。
2. **九宫格**：Grounding DINO 框出桌子，SAM2 抠出桌面，拟合桌子远边得到两个角；近边在画面外，用 `near_y`（近边纵坐标，默认 1.10）和 `spread`（近边宽度是远边的几倍，默认 1.25）描述。四个角透视后均分 3×3。
3. **物体位置**：Grounding DINO 找物体，框底边中点（物体与桌面接触处）落在哪一格就是实际位置。
4. **判定**：任一物体差 2 格及以上，或 2 个及以上物体各差 1 格，判 FAIL；首帧找不到桌子判 NEEDS_REVIEW。扁平／大件物体、同一条数据里英文名重复的物体不判；「正左／正右」按左中／右中算。

SOP 要求优先取数据集 metadata 的 `diversity`，否则读源 MCAP 的 `sop_topic`（默认 `/diversity_data`）。

## 运行

```bash
edc run /path/to/episode.mcap --robot configs/robots/x1pro_mcap.yaml --algorithm Initial_Placement_Assessment --output /path/to/results
```

机器人 yaml 的 `checks.Initial_Placement_Assessment`：

| 参数 | 说明 |
|---|---|
| `channel` | 看桌面的相机，如 `rgb/head` |
| `detector` | Grounding DINO 模型目录（grounding-dino-tiny） |
| `segmenter` | SAM2 权重（sam2.1_hiera_small.pt）；`segmenter_config` 默认 `configs/sam2.1/sam2.1_hiera_s.yaml` |
| `model` | Qwen3-VL 模型目录，可选，只翻译新物体名；和人手干预共用，同一次运行只加载一次 |
| `near_y`、`spread` | 近边参数，见上 |

结果在 `result.json`：`verdict`、`reason`、`sop_diversity`，以及每个物体的 `sop_cell`、`actual_cell`、`cells_off`、`score`、`box`；首帧存为 `initial_frame.jpg`。

## 江西数据验证（2026-11-26，269 条）

| | 条数 | FAIL |
|---|---|---|
| 人工标了 S08 | 22 | 12 |
| 人工没标 | 247 | 27 |

- 和接入前的脚本逐条比：241 条共同数据中 238 条一致。不同的 3 条都是原脚本报警、现在通过（2 条原来是误报，1 条人工标了 S08 的现在漏了）。原因是输入画面不同：原脚本读存成 JPEG 的首帧，现在直接用解码画面，压在阈值边上的检测会翻转。
- 原脚本没跑过的 28 条里报了 7 条，未人工核对。
- 人工标了但没报的主要是干扰物问题（本版不查）和 1 条疑似误标。

## 局限

- 近边参数是在这批数据（同一种桌子和机位）上估的，换场景要重新估 `near_y`、`spread`。
- 只查 SOP 写了格子的物体；干扰物没放、没换不查。
