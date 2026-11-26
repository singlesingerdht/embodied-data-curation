# Initial placement assessment (Initial_Placement_Assessment)

[简体中文](README.zh-CN.md)

Checks whether the objects named in the SOP sit in their SOP nine-grid cells in the first frame.

1. **Object names**: SOP field names map to English category names and a flat/large flag through `object_names.json` (editable); unseen names are translated by a local Qwen3-VL and written back.
2. **Grid**: Grounding DINO finds the table, SAM2 segments it and the far edge is fitted; `near_y` (default 1.10) and `spread` (default 1.25) describe the off-screen near edge. The rectified table is split 3×3.
3. **Position**: the bottom centre of each Grounding DINO box gives the observed cell.
4. **Verdict**: one object two cells off, or two objects one cell off each, gives FAIL; no table gives NEEDS_REVIEW. Flat/large objects and duplicate names are not judged.

SOP requirements come from dataset metadata `diversity`, else `sop_topic` (default `/diversity_data`) in the source MCAP.

```bash
edc run /path/to/episode.mcap --robot configs/robots/x1pro_mcap.yaml --algorithm Initial_Placement_Assessment --output /path/to/results
```

Parameters under `checks.Initial_Placement_Assessment`: `channel`, `detector` (Grounding DINO directory), `segmenter` (SAM2 checkpoint), optional `segmenter_config`, `model` (Qwen3-VL, shared with Hand_Intervention), `near_y`, `spread`.

**Validation (Jiangxi X1 Pro, 269 episodes, 2026-11-26):** 12 of 22 human-labelled S08 episodes and 27 of 247 unlabelled episodes give FAIL. 238 of 241 episodes match the pre-integration script; the 3 differences come from using the decoded frame instead of a JPEG copy. The near-edge parameters were fitted on this table and camera only.
