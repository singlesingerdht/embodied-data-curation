"""Module 4: VLM 上帝视角裁决。

触发器只能给出物理层面的碰撞先验, 是否真的判定为"任务失败"
需要 VLM 看前后 2 秒视频片段 + 物理先验自然语言后做语义判断:
  - Task Failure (失败: 真实破坏性接触)
  - Allowed Contact (允许: 正常操作中的合理接触, 如轻碰)
  - Inconclusive (无法判定)

裁决失败的事件 → 进碰撞计数; 严重者熔断 DO_NOT_USE。
"""
from __future__ import annotations

import base64
from pathlib import Path

import numpy as np

_QWEN_MODEL = None

_ADJ_PROMPT = """你是策略盲的刚体接触视觉观察员。任务文本仅帮助识别对象名称，绝不能
用于判断接触是否合理，也不能因为动作看起来像倒水、插入或抓取就改写视觉事实。
任务描述: {task}

碰撞引擎的物理先验 (基于 3D 椭球-平面/椭球-椭球几何检测, 非视觉猜测):
{prior}

结构化深度/运动证据:
{evidence}

任务物体可观测性先验:
{observability}
如果任务物体处于 OCCLUDED/REIDENTIFYING，或提示当前片段无法可靠观察任务物体，
不得把“视频里没看到碰撞”当作安全证据，应优先回答 Inconclusive。

你只负责观察刚体边界是否接触，不负责判断该接触是否符合任务。请重点比较接触前后，
确认两个实体之间的可见间隙是否由有变无；若它们已持续贴靠，则寻找新的相对速度、姿态
或受力反应突变。轻碰不要求撞倒、损坏或剧烈弹动。
液体、粉末等任务介质的流动不属于刚体碰撞；正常抓取目标、物体稳定放在支撑面、仅仅
靠近、透视重叠而深度明显不同，回答 Allowed Contact。实体被遮挡、接触边界看不清则
回答 Inconclusive。悬停、倾倒、插入前对准等语义不能豁免真实边界接触；同样，二维 mask
贴边或重叠也不能单独证明接触。不得仅凭“椭球穿透”文字判断。
运动反应、姿态突变、掉落或被推移会提高 Collision 置信度，但不是轻碰成立的必要条件。
重要：候选器以高召回为目标，许多候选是假的；红框、SAM mask 接近、椭球相交和深度
一致都不能证明碰撞。不要迎合物理先验。对刚性实体不得臆测“挤压形变”。回答
Collision 时，理由必须明确描述：哪个物体沿什么方向运动，以及可见间隙在
片段中的何时由有变无；若无法指出首次接触时序，只能回答 Inconclusive，而不是 Collision。
深度一致只能证明两物体可能处于同一接触层，不能单独证明碰撞；深度不一致会降低几何
候选可信度，但若放大视频清楚显示接触和受力反应，仍以视觉时序证据为准。
单帧或短时的三维中心位移可能来自深度抖动，不能当作物体受力；夹爪—小部件事件必须还能
看到该部件相对背景的二维独立运动。若只有“位移若干厘米”的数值而画面中物体未动，应回答
Inconclusive。相机整体运动时应比较两物体的相对运动，不得用共同像素漂移确认碰撞。

必须只回答一个 JSON 对象：
{{
  "verdict": "Collision | Allowed Contact | Inconclusive",
  "confidence": 0.0,
  "subject_identity_clear": false,
  "object_identity_clear": false,
  "pre_contact_gap_visible": false,
  "contact_visible": false,
  "first_contact_frame": null,
  "relative_reaction_visible": false,
  "reaction_frame": null,
  "contact_strength": "none | light | clear",
  "reason": "一句话"
}}
只有能指出接触前间隙和首次接触帧时才能回答 Collision。候选时间附近的连续帧以原始
时间顺序提供；若存在多个候选簇，中间可能有时间跳跃，不得把跨簇变化当成接触。"""


class VLMAdjudicator:
    """Qwen3-VL 视频 + 物理先验 → 最终裁决。"""

    def __init__(self, model_path: str | None = None, device_map="cuda:0"):
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        import torch
        self.torch = torch
        path = model_path or _QWEN_MODEL
        self.processor = AutoProcessor.from_pretrained(path)
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            path, torch_dtype=torch.bfloat16, device_map=device_map)

    def judge(self, event, clip_path: Path, task_desc: str,
              observability: dict | None = None,
              task_policy: dict | None = None) -> dict:
        import cv2
        cap = cv2.VideoCapture(str(clip_path))
        frames = []
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        clip_start = float(event.detail.get("clip_start_sec", 0.0))
        candidates = list(event.detail.get("evidence_hypothesis_times_sec",
                                           event.detail.get(
                                               "candidate_times_sec",
                                               [event.time_sec])))
        if len(candidates) > 5:
            positions = np.linspace(0, len(candidates) - 1, 5, dtype=int)
            candidates = [candidates[i] for i in positions]
        indices = []
        dense = bool(event.detail.get("dense_temporal_sampling"))
        step = max(1, int(round(fps * 0.25))) if dense else 1
        radius = 3 if dense else 4
        for candidate in candidates:
            center = int(round((float(candidate) - clip_start) * fps))
            indices.extend(center + offset * step
                           for offset in range(-radius, radius + 1))
        indices = sorted(set(max(0, min(n_frames - 1, idx)) for idx in indices))
        for frame_idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
            ok, f = cap.read()
            if not ok:
                continue
            frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        cap.release()
        if not frames:
            return {"label": "Inconclusive", "reason": "clip 解码失败"}
        prior = event.message
        if event.detail.get("roi_xyxy"):
            prior += ("\n视频左侧是全景并以红框标出候选接触区，右侧是该区域同步放大；"
                      "候选附近连续帧已被保留。请逐帧比较接触前后的间隙、轻微反应或被推移。")
        obs = observability or {}
        evidence_keys = ("center_gap", "target_reaction_m", "track_box_gap_px",
                         "prior_box_gap_px", "closing_speed_px_s",
                         "first_contact_transition", "target_step_m",
                         "obstacle_step_m",
                         "subject_step_m", "penetration_growth_m",
                         "tracker_motion_px",
                         "prior_noncontact_scans",
                         "relative_reaction_px",
                         "relative_depth_gap_m", "depth_contact_consistent",
                         "depth_valid", "depth_source", "depth_confidence",
                         "sam_scores", "motion_state",
                         "candidate_count", "candidate_times_sec",
                         "evidence_hypothesis_times_sec",
                         "segment_start_sec", "segment_end_sec")
        evidence = {key: event.detail[key] for key in evidence_keys
                    if key in event.detail}
        prompt = _ADJ_PROMPT.format(task=task_desc or "(未知)", prior=prior,
                                    evidence=evidence,
                                    observability=obs,
                                    task_policy=task_policy or {})
        msg = [{"role": "user", "content":
                [{"type": "video", "video": frames, "fps": min(15, fps)},
                 {"type": "text", "text": prompt}]}]
        text = self.processor.apply_chat_template(msg, tokenize=False,
                                                  add_generation_prompt=True)
        inputs = self.processor(text=[text], padding=True, return_tensors="pt").to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=256, do_sample=False)
        gen = self.processor.batch_decode(
            out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
        verdict = self._parse(gen)
        verdict["raw_output"] = gen
        if task_policy:
            from .contact_policy import ContactPolicyEngine
            from .task_graph import TaskGraph
            graph = TaskGraph.from_dict(task_policy)
            decision = ContactPolicyEngine().decide(
                verdict, graph, event.subject, event.object_, event.detail)
            verdict["observer_label"] = verdict["label"]
            verdict["label"] = decision.label
            verdict["is_collision"] = decision.is_collision
            verdict["policy_disposition"] = decision.policy_disposition
            verdict["policy_reason"] = decision.reason
        visual_override = self._visual_can_override_observability(verdict, event)
        scale = float(obs.get("confidence_scale", 1.0))
        if visual_override:
            scale = max(scale, 0.7)
        verdict["raw_confidence"] = verdict.pop("confidence")
        verdict["confidence_scale"] = scale
        verdict["confidence"] = round(verdict["raw_confidence"] * scale, 3)
        verdict["observability"] = obs
        verdict["visual_observability_override"] = visual_override
        if ((not obs.get("task_target_visible", True)
                or obs.get("collision_valid") is False) and not visual_override):
            verdict["label"] = "Inconclusive"
            verdict["confidence"] = min(verdict["confidence"], 0.35)
            verdict["reason"] = ("当前视频片段无法可靠观察任务物体；" +
                                 (verdict["reason"] or "无法确认安全事件"))
        elif verdict["label"] == "Task Failure" and not self._visual_contact_confirmed(verdict):
            verdict["label"] = "Inconclusive"
            verdict["is_collision"] = False
            verdict["confidence"] = min(verdict["confidence"], 0.49)
            verdict["reason"] = ("VLM没有同时给出可见的接触前间隙、明确首次接触帧和"
                                 "可靠对象身份；保留复核，不计为碰撞。")
        elif (verdict["label"] == "Task Failure"
              and not visual_override
              and not self._has_collision_evidence(event)):
            verdict["label"] = "Inconclusive"
            verdict["is_collision"] = False
            verdict["confidence"] = min(verdict["confidence"], 0.49)
            verdict["reason"] = ("VLM认为画面可能接触，但缺少机器可验证的首次接触/"
                                 "深度增长/独立运动证据；保留复核，不计为碰撞。")
        return verdict

    @staticmethod
    def _visual_contact_confirmed(verdict: dict) -> bool:
        from .contact_policy import ContactPolicyEngine
        return ContactPolicyEngine.visual_onset(verdict)

    @classmethod
    def _visual_can_override_observability(cls, verdict: dict, event) -> bool:
        """Dense visual onset may supersede a tracker-only occlusion prior."""
        return (bool(event.detail.get("occlusion_bridge"))
                and bool(event.detail.get("dense_temporal_sampling"))
                and cls._visual_contact_confirmed(verdict)
                and float(verdict.get("confidence", 0.0)) >= 0.7)

    @staticmethod
    def _has_collision_evidence(event) -> bool:
        """防止 VLM 被高召回先验诱导后凭空声称“间隙消失”。"""
        d = event.detail
        state = str(d.get("motion_state", ""))
        if event.trigger == "penetration":
            return (float(d.get("penetration_growth_m", 0.0)) >= 0.002
                    and float(d.get("subject_step_m", 0.0)) >= 0.002)
        involves_lid = ("lid" in event.object_.lower()
                        or "lid" in event.subject.lower())
        involves_gripper = ("gripper" in event.object_.lower()
                            or "gripper" in event.subject.lower())
        if involves_lid and involves_gripper:
            return (bool(d.get("roi_includes_both"))
                    and float(d.get("tracker_motion_px", 0.0)) >= 1.5)
        if "aux_sam3" in state:
            return (str(d.get("primary_status", "")).lower() in {
                        "occluded", "reidentifying"}
                    and bool(d.get("first_contact_transition"))
                    and float(d.get("relative_reaction_px", 0.0)) >= 8.0)
        if state in {"bonded", "driven"}:
            motion = max(float(d.get("subject_step_m", 0.0)),
                         float(d.get("target_step_m", 0.0)))
            return (bool(d.get("first_contact_transition"))
                    and motion >= 0.002
                    and d.get("depth_contact_consistent") is not False)
        if state == "sam3_2d_contact":
            return (bool(d.get("first_contact_transition"))
                    and float(d.get("target_step_m", 0.0)) >= 0.002
                    and float(d.get("closing_speed_px_s", 0.0)) >= 2.0
                    and d.get("depth_contact_consistent") is not False)
        return bool(d.get("first_contact_transition"))

    @staticmethod
    def _parse(text: str) -> dict:
        import re
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                raw = __import__("json").loads(match.group(0))
                value = str(raw.get("verdict", "Inconclusive")).lower()
                label = ("Task Failure" if value == "collision"
                         else "Allowed Contact" if value == "allowed contact"
                         else "Inconclusive")
                return {"label": label,
                        "confidence": min(1.0, max(0.0, float(
                            raw.get("confidence", 0.5)))),
                        "reason": str(raw.get("reason", "")),
                        "is_collision": label == "Task Failure",
                        **{key: raw.get(key) for key in (
                            "subject_identity_clear", "object_identity_clear",
                            "pre_contact_gap_visible", "contact_visible",
                            "first_contact_frame", "relative_reaction_visible",
                            "reaction_frame", "separation_after_contact_visible",
                            "contact_strength")}}
            except (ValueError, TypeError):
                pass
        label = "Inconclusive"
        reason = ""
        confidence = 0.5
        for line in text.splitlines():
            clean = line.replace("**", "").strip()
            low = clean.lower()
            # Qwen 偶尔省略字段名，直接输出 ``Allowed Contact: ...``。
            if re.match(r"^allowed contact\s*[:：]", low):
                label = "Allowed Contact"
                reason = re.split(r"[:：]", clean, maxsplit=1)[1].strip()
                continue
            if re.match(r"^collision\s*[:：]", low):
                label = "Task Failure"
                reason = re.split(r"[:：]", clean, maxsplit=1)[1].strip()
                continue
            if re.match(r"^inconclusive\s*[:：]", low):
                label = "Inconclusive"
                reason = re.split(r"[:：]", clean, maxsplit=1)[1].strip()
                continue
            if re.match(r"^label\s*[:：]", low):
                v = re.split(r"[:：]", clean, maxsplit=1)[1].strip().lower()
                if "collision" in v or "failure" in v:
                    label = "Task Failure"
                elif "allow" in v:
                    label = "Allowed Contact"
                elif "允许" in v or "正常接触" in v:
                    label = "Allowed Contact"
                elif "碰撞" in v:
                    label = "Task Failure"
                else:
                    label = "Inconclusive"
            elif re.match(r"^reason\s*[:：]", low):
                reason = re.split(r"[:：]", clean, maxsplit=1)[1].strip()
            elif re.match(r"^confidence\s*[:：]", low):
                try:
                    value = re.split(r"[:：]", clean, maxsplit=1)[1].strip()
                    confidence = min(1.0, max(0.0, float(value)))
                except ValueError:
                    pass
        return {"label": label, "confidence": confidence, "reason": reason,
                "is_collision": label == "Task Failure"}


class MockAdjudicator:
    """离线 mock: 所有事件标记为 Inconclusive (跳过 GPU 加载)。"""

    def judge(self, event, clip_path, task_desc, observability=None,
              task_policy=None):
        return {"label": "Inconclusive", "confidence": 0.0,
                "observability": observability or {}, "reason": "mock 未裁决"}
