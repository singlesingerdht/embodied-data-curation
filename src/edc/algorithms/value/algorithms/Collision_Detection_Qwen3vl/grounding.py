"""Module 1: 帧 0 语义接地 (VLM 盘点 + SAM3 精分割) → SceneGraph。

策略:
  1) Qwen3-VL 看帧 0 + 任务文本 → 语义清单 (名称/类别/框):
     A_gripper(夹爪) / A_target(目标物) / B_env(桌面/墙) / C_obstacle(被动障碍物)
  2) SAM3 对每个物体用 文本概念+几何框 提示 → 精确 2D mask
     (VLM 框失准时, 文本提示兜底; 两者都失败才丢弃该实体)
  3) mask+depth 反投影 → 3D: B 类拟平面, A/C 类拟 MVEE, C 类缓存 C_init
  4) 附加红利: 帧 0 夹爪 mask 的 3D 中心 与 本体感知 ee 投影的差
     = TCP→夹爪质心的固定偏移 (一次性标定, 后续全靠本体感知高频更新)
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

_CATEGORY_PROMPT = """You are a robot manipulation scene analyst. Look at the FIRST frame of a robot manipulation video.
Task: {task}

Identify the visible objects and classify each into EXACTLY one category:
- "A_gripper": the robot end-effector / gripper (each visible gripper arm separately)
- "A_target": the specific object(s) being GRABBED, MOVED, or MANIPULATED by the robot per the task.
  IMPORTANT: The target is the object the robot picks up and moves — NOT its container or holder.
  For example, if the task is "grab the lid and place tissue in the box", list BOTH the LID and the TISSUE as separate A_target entries.
  The tissue box itself is NOT a target (it stays on the table as a container → B_env).
  List each distinct manipulated object as a separate entry.
- "B_env": background infrastructure: the tabletop, walls, fixed structures, AND containers that stay in place
  (e.g. the tissue box if the task manipulates items inside/on it).
- "C_obstacle": other passive movable objects on/near the table that are NOT the target (cups, bottles, tools, etc.)

Respond with ONLY a JSON array, no other text:
[
  {{"name": "<short english name>", "category": "<A_gripper|A_target|B_env|C_obstacle>", "box_xyxy": [x1, y1, x2, y2]}}
]
Rules: box in pixels [x1,y1,x2,y2] of the {w}x{h} image. If the scene has no C_obstacle, return an empty list for them. Table must be included as B_env."""

_QWEN_MODEL = None


# ─────────────────────────── VLM 接地 ───────────────────────────

class VLMGrounding:
    """Qwen3-VL 帧 0 语义盘点。输出严格 JSON 实体清单。"""

    def __init__(self, model_path: str | None = None, device_map="cuda:0", *, shared_model=None):
        if shared_model is not None:
            self.torch = shared_model.torch
            self.processor = shared_model.processor
            self.model = shared_model._network
            return
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        import torch
        self.torch = torch
        path = model_path or _QWEN_MODEL
        self.processor = AutoProcessor.from_pretrained(path)
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            path, torch_dtype=torch.bfloat16, device_map=device_map)

    def detect(self, frame_rgb: np.ndarray, task_desc: str) -> list[dict]:
        h, w = frame_rgb.shape[:2]
        prompt = _CATEGORY_PROMPT.format(task=task_desc or "(unknown task)", w=w, h=h)
        msg = [{
            "role": "user",
            "content": [
                {"type": "image", "image": frame_rgb},
                {"type": "text", "text": prompt},
            ],
        }]
        text = self.processor.apply_chat_template(msg, tokenize=False,
                                                  add_generation_prompt=True)
        inputs = self.processor(text=[text], images=[frame_rgb], padding=True, return_tensors="pt").to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=1024, do_sample=False)
        gen = self.processor.batch_decode(
            out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
        evidence=getattr(self,'evidence_dir',None)
        if evidence is not None:
            from PIL import Image
            evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
            (evidence/'prompt.txt').write_text(prompt)
            (evidence/'response.txt').write_text(gen)
            Image.fromarray(frame_rgb).save(evidence/'frame.png')
        return self._parse(gen, w, h)

    @staticmethod
    def _parse(text: str, w: int, h: int) -> list[dict]:
        """鲁棒 JSON 解析: 剥 markdown 围栏 → 正则截 [...] → 逐条校验。"""
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        try:
            items = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
        out = []
        for it in items if isinstance(items, list) else []:
            try:
                box = [float(v) for v in it["box_xyxy"]]
                if not (len(box) == 4 and box[2] > box[0] and box[3] > box[1]):
                    continue
                box = [max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])]
                if it.get("category") in ("A_gripper", "A_target", "B_env", "C_obstacle"):
                    out.append({"name": str(it.get("name", "obj"))[:40],
                                "category": it["category"], "box_xyxy": box})
            except (KeyError, TypeError, ValueError):
                continue
        return out


class MockVLMGrounding:
    """离线 mock: 手工标注帧 0 (episode_0008 校准用)。"""

    def __init__(self, items: list[dict]):
        self.items = items

    def detect(self, frame_rgb, task_desc):
        return self.items


# ─────────────────────────── SAM3 分割 ───────────────────────────

class SAM3Grounding:
    """SAM3 帧 0 精分割: 文本概念 + VLM 几何框 双提示。"""

    def __init__(self, ckpt: str | None = None):
        if not ckpt:
            raise ValueError("Explicit SAM3 checkpoint required")
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor
        model = build_sam3_image_model(checkpoint_path=ckpt, load_from_HF=False,
                                       device="cuda", eval_mode=True)
        self.processor = Sam3Processor(model)

    def segment(self, frame_rgb: np.ndarray, concept: str,
                box_xyxy: list | None = None) -> dict:
        """返回该概念的最佳实例 {mask(H,W)bool, box, score}。

        策略 (text-first):
          1. SAM3 文本检测全部实例 (主路径);
          2. 有 VLM 框 → 选 IoU 最大且 mask 中心在框内的实例;
          3. 无 VLM 框 → 选分数最高的实例;
          4. 文本检测无结果 → box prompt 兜底 (选中心最接近框中心的 mask,
             不盲目取 mask[0]);
          5. 全失败 → 返回空。
        """
        import torch
        from PIL import Image
        pil = Image.fromarray(frame_rgb)
        H, W = frame_rgb.shape[:2]
        def _tn(x):
            return x.detach().float().cpu().numpy() if isinstance(x, torch.Tensor) else np.asarray(x)
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            state = self.processor.set_image(pil)
            res = self.processor.set_text_prompt(concept, state)
            masks, boxes, scores = res["masks"], res["boxes"], res["scores"]
            masks_np, boxes_np, scores_np = _tn(masks), _tn(boxes), _tn(scores)

        # 路径1: 文本检测 (优先)
        if len(masks_np) > 0:
            if box_xyxy is None:
                i = int(np.argmax(scores_np))
            else:
                ious = [_iou(box_xyxy, b) for b in boxes_np]
                order = np.argsort(ious)[::-1]
                i = -1
                for ci in order:
                    m = masks_np[ci]
                    if m.ndim == 4:
                        m = m[0]
                    if m.ndim == 3:
                        m = m[0]
                    if m.sum() < 200:
                        continue
                    ys, xs = np.where(m)
                    if len(xs) == 0:
                        continue
                    mcx, mcy = float(np.median(xs)), float(np.median(ys))
                    bx1, by1, bx2, by2 = box_xyxy
                    if bx1 <= mcx <= bx2 and by1 <= mcy <= by2:
                        i = ci
                        break
                if i < 0:
                    if max(ious) >= 0.05:
                        i = int(np.argmax(ious))
                    else:
                        i = int(np.argmax(scores_np))
            m = masks_np[i]
            if m.ndim == 4:
                m = m[0]
            if m.ndim == 3:
                m = m[0]
            return {"mask": m.astype(bool), "box": boxes_np[i].tolist(),
                    "score": float(scores_np[i])}

        # 路径2: box prompt 兜底 (文本检测无结果时)
        if box_xyxy is not None:
            x1, y1, x2, y2 = box_xyxy
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            bw, bh = (x2 - x1), (y2 - y1)
            norm_box = [cx / W, cy / H, bw / W, bh / H]
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                state2 = self.processor.set_image(pil)
                res_box = self.processor.add_geometric_prompt(norm_box, True, state2)
            box_masks = res_box.get("masks")
            if box_masks is not None and len(box_masks) > 0:
                bm = (box_masks.detach().float().cpu().numpy()
                      if isinstance(box_masks, torch.Tensor)
                      else np.asarray(box_masks))
                bx_c = (box_xyxy[0] + box_xyxy[2]) / 2
                by_c = (box_xyxy[1] + box_xyxy[3]) / 2
                best_i, best_d = -1, 1e9
                for mi in range(len(bm)):
                    mm = bm[mi]
                    if mm.ndim >= 3:
                        mm = mm[0]
                    if mm.sum() < 200:
                        continue
                    ys, xs = np.where(mm)
                    if len(xs) == 0:
                        continue
                    mcx, mcy = float(np.median(xs)), float(np.median(ys))
                    d = (mcx - bx_c) ** 2 + (mcy - by_c) ** 2
                    if d < best_d:
                        best_d, best_i = d, mi
                if best_i >= 0:
                    mm = bm[best_i]
                    if mm.ndim >= 3:
                        mm = mm[0]
                    return {"mask": mm.astype(bool), "box": box_xyxy,
                            "score": 0.8}

        return {"mask": None, "box": None, "score": 0.0}

    def segment_all(self, frame_rgb: np.ndarray, concept: str,
                    max_box_width_ratio: float = 0.6) -> list[dict]:
        """返回该概念的所有实例 (过滤宽框误检)。

        Args:
            max_box_width_ratio: 框宽超过画面宽此比例的实例视为误检, 过滤掉。
                                 (SAM3 有时会把多个物体框成一个大框)
        Returns:
            [{mask, box, score}, ...] 按分数降序
        """
        import torch
        from PIL import Image
        pil = Image.fromarray(frame_rgb)
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            state = self.processor.set_image(pil)
            res = self.processor.set_text_prompt(concept, state)
        masks, boxes, scores = res["masks"], res["boxes"], res["scores"]
        if len(masks) == 0:
            return []
        H, W = frame_rgb.shape[:2]

        def _tn(x):
            return x.detach().float().cpu().numpy() if isinstance(x, torch.Tensor) else np.asarray(x)
        masks = _tn(masks); boxes = _tn(boxes); scores = _tn(scores)
        out = []
        for i in range(len(masks)):
            bw = boxes[i][2] - boxes[i][0]
            if bw > W * max_box_width_ratio:
                continue  # 过滤横跨型误检
            m = masks[i]
            if m.ndim == 4:
                m = m[0]
            if m.ndim == 3:
                m = m[0]
            if m.sum() < 200:
                continue
            out.append({"mask": m.astype(bool), "box": boxes[i].tolist(),
                        "score": float(scores[i])})
        out.sort(key=lambda x: -x["score"])
        return out


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[:4]
    bx1, by1, bx2, by2 = b[:4]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


# ─────────────────────────── 场景图 ───────────────────────────

@dataclass
class SceneEntity:
    """场景实体: 类别 A_gripper/A_target/B_env/C_obstacle。"""
    eid: str
    name: str
    category: str
    mask: np.ndarray = None            # (H,W) bool 帧 0 mask
    box: list = None                   # 帧 0 xyxy
    pointcloud: np.ndarray = None      # (N,3) 帧 0 反投影点云 (米)
    ellipsoid: Any = None              # A/C 类: Ellipsoid
    plane: Any = None                  # B 类: Plane
    c_init: np.ndarray = None          # C 类: 初始中心
    bonded_to: str = None              # 抓取绑定后指向 partner eid
    tracker_ids: list = field(default_factory=list)  # CoTracker 查询点索引段
    valid: bool = True

    @property
    def is_plane(self) -> bool:
        return self.category == "B_env"


class SceneGraph:
    """实体容器 + 建图。"""

    def __init__(self):
        self.entities: dict[str, SceneEntity] = {}

    def add(self, e: SceneEntity):
        self.entities[e.eid] = e

    def by_category(self, cat: str) -> list[SceneEntity]:
        return [e for e in self.entities.values() if e.category == cat and e.valid]

    def planes(self):
        return self.by_category("B_env")

    def obstacles(self):
        return self.by_category("C_obstacle")

    def grippers(self):
        return self.by_category("A_gripper")

    def targets(self):
        return self.by_category("A_target")
