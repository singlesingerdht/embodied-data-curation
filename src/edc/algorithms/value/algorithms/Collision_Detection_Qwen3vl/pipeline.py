"""Phase 1→3 编排: VLM接地 + SAM3分割 + CoTracker跟踪 + 本体感知 + 碰撞引擎。

流程:
  Phase1 (帧0):  VLM 盘点 → SAM3 mask → 反投影 → 平面/MVEE → 夹爪 TCP 标定
  Phase2 (1..N): CoTracker 全视频轨迹 → 每检测帧 深度查询+投影 → 椭球更新;
                 夹爪椭球 = 本体感知呼吸模型 (250Hz ee+gripper);
                 闭合+相交 → bonded, 之后 target 随 ee 增量平移
  Phase3 (触发时): 事件 + 前后2s clip → VLM 裁决 (失败接触 / 允许接触)
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from .collision_engine import CollisionEngine, CollisionEvent

from .geometry import (Ellipsoid, fit_extent_ellipsoid, fit_mvee,
                        fit_mvee_robust, fit_plane_ransac)
from .gripper_model import GripperModel, detect_grasp, GRASP_CLOSE_TH
from .grounding import (MockVLMGrounding, SceneEntity, SceneGraph, SAM3Grounding,
                        VLMGrounding)
from .tracker import CoTracker3D, Entity3DTracker, sample_mask_points
from .appearance import AppearanceConfig, AppearanceMatcher
from .task_graph import TaskGraph, TaskSemanticCompiler

_PLANE_MIN_PTS = 800     # 平面拟合最少点数
_ELL_MIN_PTS = 40        # 椭球拟合最少点数
_SUBSAMPLE = {           # 反投影下采样 (点太多时)
    "B_env": 6, "C_obstacle": 3, "A_target": 3, "A_gripper": 2,
}


def _coherent_track_motion(uv0: np.ndarray, uv1: np.ndarray,
                           visibility: np.ndarray,
                           min_inlier_ratio: float = 0.65) -> bool:
    """判断点群是否仍像同一个刚性实例，而不是少量点漂到邻近物体。

    用 RANSAC 部分仿射模型允许平移、旋转和尺度变化；只有大多数可见点
    支持同一个运动时，CoTracker 才能在超过静态闸门后继续驱动实体。
    """
    import cv2
    keep = np.asarray(visibility) > 0.5
    src = np.asarray(uv0, np.float32)[keep]
    dst = np.asarray(uv1, np.float32)[keep]
    if len(src) < 12:
        return False
    _, inliers = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=6.0,
        maxIters=1000, confidence=0.99)
    return (inliers is not None and
            float(np.mean(inliers.reshape(-1) > 0)) >= min_inlier_ratio)


def _visible_track_box(points: np.ndarray, visibility: np.ndarray,
                       min_points: int = 8) -> list[float] | None:
    keep = np.asarray(visibility) > 0.5
    pts = np.asarray(points, dtype=float)[keep]
    if len(pts) < min_points:
        return None
    lo, hi = np.percentile(pts, [5, 95], axis=0)
    return [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])]


def _box_gap(a: list[float], b: list[float]) -> float:
    dx = max(0.0, a[0] - b[2], b[0] - a[2])
    dy = max(0.0, a[1] - b[3], b[1] - a[3])
    return float(np.hypot(dx, dy))


def _point_box_gap(point: np.ndarray, box: list[float]) -> float:
    """Pixel distance from a projected TCP point to a tracked object box."""
    x, y = float(point[0]), float(point[1])
    dx = max(0.0, float(box[0]) - x, x - float(box[2]))
    dy = max(0.0, float(box[1]) - y, y - float(box[3]))
    return float(np.hypot(dx, dy))


def _mask_iou(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    a, b = np.asarray(mask_a, bool), np.asarray(mask_b, bool)
    union = int(np.count_nonzero(a | b))
    return float(np.count_nonzero(a & b) / union) if union else 0.0


def _select_distinct_initial_candidate(candidates: list[dict],
                                       claimed_masks: list[np.ndarray],
                                       max_iou: float = 0.35) -> dict | None:
    """从同一语义的 SAM 多候选中选择未被其他实例占用的 mask。"""
    valid = []
    for candidate in candidates:
        mask = candidate.get("mask")
        if mask is None or np.count_nonzero(mask) < 400:
            continue
        overlap = max((_mask_iou(mask, old) for old in claimed_masks),
                      default=0.0)
        if overlap <= max_iou:
            valid.append(candidate)
    return max(valid, key=lambda item: float(item.get("score", 0.0)),
               default=None)


def _initial_identity_can_overlap(name_a: str, name_b: str,
                                  task_graph: TaskGraph | None = None) -> bool:
    """Only compiler-approved component/owner pairs may share an initial mask."""
    if task_graph is not None:
        return task_graph.can_overlap(name_a, name_b)
    # Backwards-compatible conservative compilation for small helper callers.
    graph = TaskSemanticCompiler().compile("", [
        {"name": name_a, "category": "A_target"},
        {"name": name_b, "category": "A_target"},
    ])
    return graph.can_overlap(name_a, name_b)


def _box_contact_transition(history: list[tuple[int, float]], frame_idx: int,
                            gap_now: float, timestamps, lookback_frames: int = 15) -> dict:
    """从框间距历史提取接近速度和“由分离到接触”证据。"""
    if not history:
        return {"prior_box_gap_px": round(gap_now, 1),
                "closing_speed_px_s": 0.0,
                "first_contact_transition": False}
    past = next((item for item in history
                 if frame_idx - item[0] >= lookback_frames), history[0])
    dt = float(timestamps[frame_idx] - timestamps[past[0]])
    speed = (past[1] - gap_now) / dt if dt > 0 else 0.0
    return {"prior_box_gap_px": round(past[1], 1),
            "closing_speed_px_s": round(speed, 1),
            "first_contact_transition": bool(past[1] > 6 and gap_now <= 6)}


def _contact_onset_after_absence(prior_noncontact: int, touching: bool,
                                 required_scans: int = 2) -> tuple[int, bool]:
    """遮挡视角中，仅在连续未接触后首次接触时触发一次。"""
    if not touching:
        return min(prior_noncontact + 1, 10), False
    return 0, prior_noncontact >= required_scans


def _mask_centroid(mask: np.ndarray) -> np.ndarray | None:
    y, x = np.nonzero(np.asarray(mask, bool))
    if len(x) < 8:
        return None
    return np.array([float(x.mean()), float(y.mean())])


def _nearest_mask_displacement(reference_mask: np.ndarray,
                               candidate_masks: list[np.ndarray]) -> float | None:
    ref = _mask_centroid(reference_mask)
    candidates = [_mask_centroid(mask) for mask in candidate_masks]
    candidates = [center for center in candidates if center is not None]
    if ref is None or not candidates:
        return None
    return min(float(np.linalg.norm(ref - center)) for center in candidates)


def _relative_pair_displacement(current_a: np.ndarray, current_b: np.ndarray,
                                prior_pairs: list[tuple[np.ndarray, np.ndarray]]) -> float | None:
    """消除腕部相机运动，比较 (B质心-A质心) 的帧间变化。"""
    ca, cb = _mask_centroid(current_a), _mask_centroid(current_b)
    if ca is None or cb is None:
        return None
    current_rel = cb - ca
    deltas = []
    for mask_a, mask_b in prior_pairs:
        pa, pb = _mask_centroid(mask_a), _mask_centroid(mask_b)
        if pa is not None and pb is not None:
            deltas.append(float(np.linalg.norm(current_rel - (pb - pa))))
    return min(deltas) if deltas else None


def _masks_within(mask_a: np.ndarray, mask_b: np.ndarray,
                  pixels: int = 6) -> bool:
    import cv2
    kernel = np.ones((2 * pixels + 1, 2 * pixels + 1), np.uint8)
    grown = cv2.dilate(mask_a.astype(np.uint8), kernel, iterations=1)
    return bool(np.any((grown > 0) & np.asarray(mask_b, dtype=bool)))


def _aux_identity_valid(subject_mask: np.ndarray,
                        counterpart_masks: list[np.ndarray],
                        similarity: float, margin: float,
                        max_cross_iou: float = 0.5,
                        min_similarity: float = 0.40,
                        min_margin: float = 0.08) -> bool:
    """Reject an auxiliary candidate duplicated under both pair concepts."""
    pot = np.asarray(subject_mask, bool)
    if similarity < min_similarity or margin < min_margin:
        return False
    for cup_mask in counterpart_masks:
        cup = np.asarray(cup_mask, bool)
        union = int(np.count_nonzero(pot | cup))
        if union and np.count_nonzero(pot & cup) / union > max_cross_iou:
            return False
    return True


# Compatibility for existing report replay/tests; new runtime uses the generic name.
_aux_teapot_identity_valid = _aux_identity_valid


def _select_cross_view_subject(candidates: list[tuple], driven_targets: set[str],
                               motion_counts: dict[str, int],
                               coupling_counts: dict[str, int] | None = None,
                               coupling_last_frame: dict[str, int] | None = None):
    """Prefer the currently manipulated hidden entity, never config order."""
    return max(candidates, key=lambda item: (
        item[0].eid in driven_targets,
        item[0].bonded_to is not None,
        int((coupling_last_frame or {}).get(item[0].eid, -1)),
        int((coupling_counts or {}).get(item[0].eid, 0)),
        int(motion_counts.get(item[0].eid, 0)),
    ), default=None)


def _mask_depth_contact_evidence(depth: np.ndarray, mask_a: np.ndarray,
                                 mask_b: np.ndarray,
                                 band_px: int = 12) -> dict:
    """计算两个 mask 接触边界处的相对深度，而非易受背景影响的整框深度。"""
    import cv2
    a, b = np.asarray(mask_a, bool), np.asarray(mask_b, bool)
    kernel = np.ones((2 * band_px + 1, 2 * band_px + 1), np.uint8)
    near_b = cv2.dilate(b.astype(np.uint8), kernel, iterations=1) > 0
    near_a = cv2.dilate(a.astype(np.uint8), kernel, iterations=1) > 0
    za = np.asarray(depth)[a & near_b]
    zb = np.asarray(depth)[b & near_a]
    za = za[np.isfinite(za) & (za > .15) & (za < 3.0)]
    zb = zb[np.isfinite(zb) & (zb > .15) & (zb < 3.0)]
    if len(za) < 8 or len(zb) < 8:
        return {"depth_valid": False, "depth_contact_consistent": None}
    med_a, med_b = float(np.median(za)), float(np.median(zb))
    gap = abs(med_a - med_b)
    # 单目 DA3 的绝对深度可能漂移，但同帧局部相对深度通常稳定；8cm
    # 仅作候选可信度和 VLM 先验，不能单独决定碰撞。
    return {"depth_valid": True,
            "contact_depth_a_m": round(med_a, 3),
            "contact_depth_b_m": round(med_b, 3),
            "relative_depth_gap_m": round(gap, 3),
            "depth_contact_consistent": gap <= .08,
            "depth_samples": [int(len(za)), int(len(zb))]}


def _collision_identity(eid: str) -> str:
    """把短时实例抖动归一到语义对象，供连续候选合并。"""
    value = eid.lower().replace("_", " ")
    if "cup" in value:
        return "cup"
    if "lid" in value:
        return "teapot_lid" if "teapot" in value else "lid"
    if "teapot" in value:
        return "teapot"
    if "kettle" in value:
        return "kettle"
    if "gripper" in value or "arm" in value:
        return "gripper"
    if "table" in value:
        return "table"
    return value


def merge_collision_events(events: list[CollisionEvent],
                           gap_sec: float = 3.0,
                           max_span_sec: float = 6.0,
                           occlusion_bridge_sec: float = 15.0,
                           task_graph: TaskGraph | None = None) -> list[CollisionEvent]:
    """将同对象对、3 秒内连续的物理/SAM 触发合并为一个 VLM 裁决段。"""
    collision_triggers = {
        "intersection", "penetration", "displacement", "grasp_unverified"}
    ordered = sorted((e for e in events if e.trigger in collision_triggers),
                     key=lambda e: e.frame_idx)
    groups: list[list[CollisionEvent]] = []
    active: dict[tuple[str, str, str], int] = {}
    for event in ordered:
        identity = (task_graph.semantic_group if task_graph is not None
                    else _collision_identity)
        key = (event.trigger, identity(event.subject), identity(event.object_))
        group_idx = active.get(key)
        previous = groups[group_idx][-1] if group_idx is not None else None
        occlusion_bridge = bool(previous is not None
            and previous.detail.get("motion_state") == "occluded_tcp_near_cup"
            and event.detail.get("motion_state") == "occluded_tcp_near_cup")
        allowed_gap = occlusion_bridge_sec if occlusion_bridge else gap_sec
        allowed_span = occlusion_bridge_sec if occlusion_bridge else max_span_sec
        if (group_idx is not None
                and event.time_sec - groups[group_idx][-1].time_sec <= allowed_gap + 1e-9
                and event.time_sec - groups[group_idx][0].time_sec <= allowed_span + 1e-9):
            groups[group_idx].append(event)
        else:
            groups.append([event])
            active[key] = len(groups) - 1
    merged = []
    for group in groups:
        # 优先用有效硬几何作为代表；否则用多视角/SAM 候选。
        representative = max(group, key=lambda e: (
            bool(e.detail.get("first_contact_transition")),
            e.detail.get("collision_valid", True),
            bool(e.detail.get("roi_xyxy")),
            "sam3" in str(e.detail.get("motion_state", "")),
            e.detail.get("motion_state") in {"bonded", "driven",
                                              "direct_lid_contact"},
            -float(e.detail.get("center_gap", 1e9)),
            e.frame_idx))
        details = dict(representative.detail)
        candidate_times = [round(e.time_sec, 3) for e in group]
        interval_midpoint = round(
            (group[0].time_sec + group[-1].time_sec) / 2.0, 3)
        interval_span = group[-1].time_sec - group[0].time_sec
        interval_quartiles = [round(group[0].time_sec + interval_span * q, 3)
                              for q in (0.25, 0.75)]
        evidence_hypotheses = [candidate_times[0],
                               candidate_times[len(candidate_times) // 2],
                               interval_midpoint,
                               candidate_times[-1],
                               round(group[-1].time_sec + 0.9, 3)]
        bridged_occlusion = (len(group) > 1 and all(
            e.detail.get("motion_state") == "occluded_tcp_near_cup"
            for e in group) and interval_span > gap_sec)
        if bridged_occlusion:
            evidence_hypotheses.extend(interval_quartiles)
        details.update({
            "segment_start_sec": round(group[0].time_sec, 2),
            "segment_end_sec": round(group[-1].time_sec, 2),
            "candidate_count": len(group),
            "candidate_times_sec": candidate_times,
            "evidence_hypothesis_times_sec": sorted(set(evidence_hypotheses)),
            "views": sorted({str(e.detail.get("view", "faceImg"))
                             for e in group}),
            "evidence_sources": sorted({str(e.detail.get(
                "motion_state", e.trigger)) for e in group}),
            "occlusion_bridge": bridged_occlusion,
            "dense_temporal_sampling": bridged_occlusion,
        })
        prior = (f"候选事件段 {details['segment_start_sec']:.2f}-"
                 f"{details['segment_end_sec']:.2f}s，共{len(group)}个触发。"
                 f"代表证据：{representative.message}")
        merged.append(CollisionEvent(
            representative.frame_idx, representative.time_sec,
            representative.trigger, representative.subject,
            representative.object_, details, prior))
    return merged


class NeuroSymbolicPipeline:
    def __init__(self, episode_dir: str, out_dir: str = "ns_collision_out",
                 stride: int = 3, cam: str = "cam_front",
                 vlm: VLMGrounding | MockVLMGrounding | None = None,
                 use_vlm_adjudicator: bool = True, debug: bool = True,
                 ep=None, enable_reid: bool = True,
                 appearance_config: AppearanceConfig | None = None,
                 appearance_encoder=None, task_graph: TaskGraph | None = None,
                 task_compiler: TaskSemanticCompiler | None = None):
        if ep is not None:
            self.ep = ep
        else:
            raise ValueError("Explicit EDC RGB-D and physical mapping required")
        self.out = Path(out_dir); self.out.mkdir(parents=True, exist_ok=True)
        self.stride = stride
        self.debug = debug
        self.graph = SceneGraph()
        self.engine = CollisionEngine()
        self.grippers: dict[str, GripperModel] = {}
        self.vlm = vlm
        self.adjudicator = None
        self.use_vlm_adjudicator = use_vlm_adjudicator
        self.events_adjudicated: list[dict] = []
        self.collision_segments: list[CollisionEvent] = []
        self.frame_callback = None  # 可视化钩子: (frame_idx, ellipsoids, events, ctx)
        self.sam3 = None
        self.enable_reid = enable_reid
        self.appearance_config = appearance_config or AppearanceConfig()
        self.appearance_encoder = appearance_encoder
        self.appearance: AppearanceMatcher | None = None
        self.track_transitions: list[dict] = []
        self.target_state_samples: list[dict] = []
        self.observability_history: dict[int, dict] = {}
        self._aux_captures: dict[str, object] = {}
        self.task_graph = task_graph
        self.task_compiler = task_compiler or TaskSemanticCompiler()

    def _aux_frame(self, view: str, frame_idx: int) -> np.ndarray | None:
        """Read the paired original auxiliary image, never seek by an assumed FPS."""
        return self.ep.aux_frame(view, frame_idx)

    # ─────────────── Phase 1: 帧 0 接地与抽象 ───────────────
    def phase1_ground(self, sam3: SAM3Grounding):
        self.sam3 = sam3
        frame0 = self.ep.rgb[0]
        items = (self.vlm or MockVLMGrounding([])).detect(frame0, self.ep.task_desc)
        self._log(f"VLM 盘点 {len(items)} 实体: "
                  + ", ".join(f"{i['name']}({i['category']})" for i in items))
        # —— Fallback: VLM 没检测到 A_target → 从任务描述抽名词短语, SAM3 逐个试 ——
        target_items = [it for it in items if it["category"] == "A_target"]
        if not target_items and self.vlm is not None:
            candidates = _task_target_phrases(self.ep.task_desc)
            self._log(f"  VLM 未检测到 A_target, SAM3 尝试短语: {candidates}")
            hit_masks = []  # (mask, score, concept) 用于去重
            for concept in candidates:
                res = sam3.segment(frame0, concept)
                if res["mask"] is None or res["mask"].sum() < 500:
                    continue
                m = res["mask"].astype(bool)
                # 去重: 与已命中掩码 IoU > 0.5 → 保留高分者
                dup = False
                for i, (m2, s2, c2) in enumerate(hit_masks):
                    inter = float((m & m2).sum())
                    iou = inter / max(1.0, float((m | m2).sum()))
                    if iou > 0.5:
                        dup = True
                        if res["score"] > s2:  # 新掩码更好 → 替换
                            old = items.index(next(it for it in items
                                                   if it["name"] == c2))
                            hit_masks[i] = (m, res["score"], concept)
                            items[old] = {"name": concept, "category": "A_target",
                                          "box_xyxy": res["box"], "_mask": m}
                            self._log(f"  (SAM3 {concept!r} 替换 {c2!r} 同区域更优)")
                        break
                if not dup:
                    hit_masks.append((m, res["score"], concept))
                    # _mask 直接存下: 主循环不再用 box prompt 重分割
                    # (几何框 prompt 的 add_geometric_prompt 会返回糊成
                    #  一团的大 blob, 把椭球拉爆)
                    items.append({"name": concept, "category": "A_target",
                                  "box_xyxy": res["box"], "_mask": m})
                    self._log(f"  (SAM3 短语 {concept!r} 命中 → A_target, "
                              f"{int(m.sum())}px, score={res['score']:.2f})")

        # Compile only after fallback discovery so every runtime entity is covered.
        if self.task_graph is None:
            self.task_graph = self.task_compiler.compile(self.ep.task_desc, items)
        self.task_graph.save(self.out / "task_graph.json")
        self._log(f"任务图 source={self.task_graph.source}, "
                  f"entities={len(self.task_graph.entities)}, "
                  f"contact_policies={len(self.task_graph.contacts)}")

        # —— Cups 多实例: segment_all("cup") → 按 u 坐标排序 → 逐个分配 ——
        cup_items = [it for it in items
                     if "cup" in it["name"].lower()
                     and it["category"] in ("C_obstacle", "A_target")
                     and it.get("_mask") is None]
        if cup_items:
            all_cups = sam3.segment_all(frame0, "cup")
            if not all_cups:
                for fb in ("teacup", "mug", "glass"):
                    all_cups = sam3.segment_all(frame0, fb)
                    if all_cups:
                        self._log(f"  (cup fallback {fb!r} 命中 {len(all_cups)} 实例)")
                        break
            if all_cups:
                all_cups.sort(key=lambda r: (r["box"][0] + r["box"][2]) / 2)
                self._log(f"  SAM3 'cup' 检测到 {len(all_cups)} 实例, "
                          f"VLM 杯子项 {len(cup_items)} 个 → 按 u 排序分配")
                for i, ci in enumerate(cup_items):
                    if i < len(all_cups):
                        ci["_mask"] = all_cups[i]["mask"]
                        ci["box_xyxy"] = all_cups[i]["box"]
                        self._log(f"  {ci['name']} → box={all_cups[i]['box']} "
                                  f"score={all_cups[i]['score']:.2f}")
                    else:
                        self._log(f"  ! {ci['name']} 无对应 SAM3 实例 (多余)")

        # SAM3: 概念 = VLM name; 匹配不到再试类别默认词
        # 备选概念列表 (逐个尝试, SAM3 对不同场景词敏感度不同)
        fallback_concepts = {
            "A_gripper": ["robotic arm", "robot hand", "mechanical arm", "robot arm"],
            "A_target": ["tissue", "paper towel", "lid", "teapot", "kettle", "cup", "object"],
            "B_env": ["table", "tabletop"],
            "C_obstacle": ["object", "cup", "bottle", "teapot", "kettle"],
        }
        # —— A_gripper 特殊处理: SAM3 多实例检测 → 按像素距离匹配各臂 ——
        gripper_instances = []  # [{mask, box, score}]
        for it in items:
            cat = it["category"]
            if cat == "A_gripper":
                # 优先用 VLM name, 失败则走 fallback 概念列表 (多实例)
                all_res = sam3.segment_all(frame0, it["name"])
                if not all_res:
                    for concept in fallback_concepts["A_gripper"]:
                        all_res = sam3.segment_all(frame0, concept)
                        if all_res:
                            self._log(f"  (gripper 概念 {concept!r} 命中 {len(all_res)} 实例)")
                            break
                gripper_instances.extend(all_res)
                continue
            # 非 gripper 实体: SAM3 fallback 目标已带 _mask, 直接复用
            # (重跑文本提示可能选到更差实例, box prompt 会糊成大 blob)
            if it.get("_mask") is not None:
                res = {"mask": it["_mask"], "box": it.get("box_xyxy"), "score": 1.0}
            else:
                res = sam3.segment(frame0, it["name"], it.get("box_xyxy"))
            if res["mask"] is None or res["mask"].sum() < 400:
                for concept in fallback_concepts.get(cat, ["object"]):
                    res = sam3.segment(frame0, concept, it.get("box_xyxy"))
                    if res["mask"] is not None and res["mask"].sum() >= 400:
                        self._log(f"  (概念 {concept!r} 命中)")
                        break
            if res["mask"] is None or res["mask"].sum() < 400:
                self._log(f"  ! 实体 {it['name']} SAM3 分割失败, 跳过")
                continue
            if cat == "A_target":
                blockers = [old.mask for old in self.graph.targets()
                            if old.mask is not None
                            and not _initial_identity_can_overlap(
                                it["name"], old.name, self.task_graph)]
                overlap = max((_mask_iou(res["mask"], old)
                               for old in blockers), default=0.0)
                if overlap > 0.50:
                    alternatives = sam3.segment_all(
                        frame0, it["name"], max_box_width_ratio=0.8)
                    replacement = _select_distinct_initial_candidate(
                        alternatives, blockers)
                    if replacement is None:
                        self._log(f"  ! 实体 {it['name']} 与已接地目标 mask "
                                  f"重叠 IoU={overlap:.2f} 且无互斥候选，跳过以"
                                  "避免污染身份模板")
                        continue
                    res = replacement
                    self._log(f"  ~ 实体 {it['name']} 初始 mask 冲突 "
                              f"IoU={overlap:.2f}，改用互斥候选 "
                              f"score={res['score']:.2f}")
            eid = f"{cat}:{it['name']}".replace(" ", "_")
            pc = self.ep.backproject(res["mask"], 0, stride=_SUBSAMPLE.get(cat, 3))
            ent = SceneEntity(eid=eid, name=it["name"], category=cat,
                              mask=res["mask"], box=res["box"], pointcloud=pc)
            try:
                if cat == "B_env":
                    # 大平面 (桌面) 才配当 B_env 平面; 小而鼓的物体
                    # (纸巾盒等容器) 拟合成"平面"会把盒顶/盒侧当碰撞边界,
                    # 盖子放盒上、纸巾进出盒都会误报穿透 → 降级为障碍物椭球
                    plane = (fit_plane_ransac(pc)
                             if len(pc) >= _PLANE_MIN_PTS else None)
                    in_pts = None
                    if plane is not None:
                        d = np.abs(pc @ plane.n + plane.d)
                        in_pts = pc[d < 0.01]
                    if plane is not None and len(in_pts) >= 3000 and \
                            (in_pts.max(0) - in_pts.min(0)).max() >= 0.6:
                        ent.plane = plane
                        self._log(f"  + {eid}: 平面 n={plane.n.round(2)} "
                                  f"d={plane.d:.3f} 内点={plane.inliers}")
                    else:
                        # 容器类 (纸巾盒): 目标物就放在里面/上面, 拟合成
                        # 平面会误报穿透, 当障碍物会误报 gripper 正常进盒。
                        # 容器碰撞需要 OBB+开口面模型 —— 暂只记录不建图。
                        self._log(f"  ~ {eid}: 非大平面(内点"
                                  f"{plane.inliers if plane else 0}), 按容器跳过碰撞")
                        continue
                else:
                    if len(pc) < _ELL_MIN_PTS:
                        self._log(f"  ! {eid} 点太少({len(pc)}), 跳过")
                        continue
                    ent.ellipsoid = fit_extent_ellipsoid(pc)
                    if cat == "C_obstacle":
                        ent.c_init = ent.ellipsoid.center.copy()
                    self._log(f"  + {eid}: MVEE c={ent.ellipsoid.center.round(2)} "
                              f"r={sorted(ent.ellipsoid.radii.round(2))}")
            except Exception as exc:
                self._log(f"  ! {eid} 抽象失败: {exc}")
                continue
            self.graph.add(ent)

        # —— A_gripper 多实例: 按像素距离匹配到各臂, 创建独立实体 ——
        if gripper_instances:
            used = set()
            for arm in self.ep.arms:
                st_arm = self.ep.arm_state_at(0, arm)
                T = self.ep.T_arm2cam[arm]
                ee_cam = T[:3, :3] @ st_arm["ee"][:3] + T[:3, 3]
                ee_uv = self.ep.project_to_pixels(ee_cam[None])[0]
                # 找最近的未用实例 (按 TCP 到框中心像素距离)
                best_i, best_d = -1, 1e9
                for i, gi in enumerate(gripper_instances):
                    if i in used:
                        continue
                    b = gi["box"]
                    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                    d = ((ee_uv[0] - cx) ** 2 + (ee_uv[1] - cy) ** 2) ** 0.5
                    if d < best_d:
                        best_d, best_i = d, i
                if best_i < 0 or best_d > 300:
                    self._log(f"  ! {arm} 未匹配到 gripper 实例 (最近 {best_d:.0f}px)")
                    continue
                used.add(best_i)
                gi = gripper_instances[best_i]
                pc = self.ep.backproject(gi["mask"], 0, stride=_SUBSAMPLE.get("A_gripper", 2))
                if len(pc) < _ELL_MIN_PTS:
                    self._log(f"  ! {arm} gripper 点太少({len(pc)})")
                    continue
                eid = f"A_gripper:{arm}"
                ent = SceneEntity(eid=eid, name=arm, category="A_gripper",
                                 mask=gi["mask"], box=gi["box"], pointcloud=pc)
                ent.ellipsoid = fit_mvee_robust(pc)
                self.graph.add(ent)
                self._log(f"  + {eid}: MVEE c={ent.ellipsoid.center.round(2)} "
                          f"r={sorted(ent.ellipsoid.radii.round(2))} "
                          f"score={gi['score']:.2f} 匹配距离={best_d:.0f}px")

        # 夹爪 TCP 标定: 视觉 mask 中心 vs 本体感知 ee 投影
        # gripper 实体已按臂命名 (A_gripper:left_arm / A_gripper:right_arm)
        for arm in self.ep.arms:
            st = self.ep.arm_state_at(0, arm)
            T = self.ep.T_arm2cam[arm]
            ee_cam = T[:3, :3] @ st["ee"][:3] + T[:3, 3]
            # 直接按 eid 匹配
            ent = self.graph.entities.get(f"A_gripper:{arm}")
            if ent is not None and ent.pointcloud is not None and len(ent.pointcloud) > 20:
                # 旋转: 臂系四元数 → 相机系旋转矩阵 → wxyz 四元数
                R_arm = _quat_rotmat(st["ee"][3:7])
                R_cam_arm = T[:3, :3] @ R_arm
                quat_cam = _rotmat_quat(R_cam_arm)
                # 过滤 mask 点云: 只留 TCP 15cm 内的点 → 去掉臂连杆, 只留夹爪
                d3d = np.linalg.norm(ent.pointcloud - ee_cam, axis=1)
                grip_pc = ent.pointcloud[d3d < 0.15]
                if len(grip_pc) < 10:
                    grip_pc = ent.pointcloud  # 过滤后太少 → 用全部
                # 标定: TCP → 夹爪视觉中心偏移
                visual_center = np.median(grip_pc, axis=0)
                offset = R_cam_arm.T @ (visual_center - ee_cam)
                # 用过滤后的点云重新拟合 MVEE 作为 base (只含夹爪, 不含臂)
                from .geometry import fit_mvee
                base = fit_mvee(grip_pc[::2]) if len(grip_pc) > 10 else None
                gm = GripperModel(arm, base_ellipsoid=base, tcp_offset=offset)
                self._log(f"  夹爪标定[{arm}]: offset={offset.round(3)} "
                          f"过滤后{len(grip_pc)}/{len(ent.pointcloud)}点 "
                          f"base_r={sorted(base.radii.round(2)) if base else '先验'}")
            else:
                gm = GripperModel(arm, base_ellipsoid=None)
                self._log(f"  夹爪[{arm}]: 无视觉, 用几何先验")
            self.grippers[arm] = gm
            # 在 graph 注册虚拟夹爪实体, 供碰撞引擎按 eid 查询 category
            gid = f"gripper:{arm}"
            if gid not in self.graph.entities:
                self.graph.add(SceneEntity(eid=gid, name=arm, category="A_gripper"))
        if self.enable_reid:
            self.appearance = AppearanceMatcher(
                self.appearance_config, encoder=self.appearance_encoder)
            for ent in self.graph.targets():
                if ent.mask is not None:
                    self.appearance.register_template(ent.eid, frame0, ent.mask)
            self._log(f"  ReID 模板: {len(self.appearance.templates)} 个 A_target")
        return self

    # ─────────────── Phase 2: 跟踪 + 逐帧评估 ───────────────
    def phase2_track(self, tracker3d: CoTracker3D):
        ep, graph = self.ep, self.graph
        # Step1: 收集活动实体 (A_target/C_obstacle) 查询点
        # B_env 平面静态; A_gripper 由本体感知驱动 (不走 CoTracker)
        queries, owner = [], []
        _n_pts = 0  # 已累积的点总数 (len(queries) 是数组个数, 不是点数!)
        for ent in list(graph.entities.values()):
            if ent.mask is None or ent.category in ("B_env", "A_gripper"):
                continue
            pts = sample_mask_points(ent.mask, n=48, seed=abs(hash(ent.eid)) % 2**31)
            ent.tracker_ids = list(range(_n_pts, _n_pts + len(pts)))
            _n_pts += len(pts)
            queries.append(pts); owner.append(ent.eid)
        if not queries:
            self._log("! 无可跟踪实体")
            return self
        queries = np.concatenate(queries)
        t0 = time.time()
        self._log(f"CoTracker3 跟踪 {len(queries)} 点 × {ep.n_frames} 帧 ...")
        tracks, visibs = tracker3d.track(ep.rgb, queries)
        self._log(f"  跟踪完成 {time.time()-t0:.1f}s → {tracks.shape}")
        np.savez_compressed(self.out / 'tracking-evidence.npz', tracks=tracks, visibility=visibs,
                            queries=queries, owners=np.asarray(owner,dtype=str))

        trk = Entity3DTracker(ep, stride=self.stride)
        # 每实体平滑中心缓存 (从帧 0 MVEE 出发)
        # 缓存初始 MVEE (排除 A_gripper — 夹爪走本体感知, 不用视觉 MVEE)
        tracked = {e.eid: e for e in graph.entities.values()
                   if e.ellipsoid is not None and e.category != "A_gripper"}
        forbidden_target_pairs: set[frozenset[str]] = set()
        if self.task_graph is not None:
            _targets = list(graph.targets())
            for _i, _first in enumerate(_targets):
                for _second in _targets[_i + 1:]:
                    _policy = self.task_graph.contact_policy(
                        _first.name, _second.name, "rigid_contact")
                    if _policy is not None and _policy.disposition == "forbidden":
                        forbidden_target_pairs.add(frozenset(
                            (_first.eid, _second.eid)))
        centers = {eid: e.ellipsoid.center.copy() for eid, e in tracked.items()}
        trusted_centers = {eid: c.copy() for eid, c in centers.items()}
        # CoTracker 的三维中心与初始 MVEE 往往有固定的深度偏差。记录第一份
        # CoTracker 中心作为视觉坐标基线，只使用相对位移驱动可信中心。
        visual_baselines: dict[str, np.ndarray] = {}
        centers_init = {eid: c.copy() for eid, c in centers.items()}
        radii = {eid: e.ellipsoid.radii.copy() for eid, e in tracked.items()}
        axes = {eid: e.ellipsoid.axes.copy() for eid, e in tracked.items()}
        bonded_prev: dict[tuple[str, str], bool] = {}  # (arm, target_eid) → 上帧是否接触
        ee_prev = {arm: None for arm in self.grippers}
        # 抓取必须伴随"主动闭合": 本帧之前张开过 (>GRASP_CLOSE_TH) 才允许判抓取,
        # 抓取触发后清零 —— 防 home 位一直闭合的夹爪被误判为抓取。
        # 用状态机而非滑窗: 夹爪可能张开后闭合接近 (闭合接近≠无意抓取)
        opened_since_grasp = {arm: False for arm in self.grippers}
        close_prev = {arm: False for arm in self.grippers}
        slip_cnt: dict[str, int] = {}          # 滑落连续帧计数
        lost_frames: dict[str, int] = {eid: 0 for eid in owner}
        reground_last: dict[str, int] = {}      # eid → 上次 SAM3 重接地帧号
        last_visual: dict[str, int] = {}         # eid → 上次视觉确认帧号 (CoTracker/SAM3)
        # bonded 跟随: eid → (arm, 锚中心, 锚ee, 锚腕旋转R0) —— 持有时目标
        # 被夹爪刚性夹持, 在夹爪系内静止: center = ee + ΔR(c0−ee0), ΔR 为腕旋转增量
        hold_anchor: dict[str, tuple] = {}
        pending: dict[str, dict] = {}   # arm → 抓取确认窗口 {tgt, until, next}
        blind_bound: set = set()          # 盲绑实体 (无视觉确认) → 跳过滑落检测
        released_at: dict[str, int] = {}  # eid → 释放帧, 释放后放宽重锚 cap
        regrounded_once: set = set()      # 已成功重锚过的 A_target (首次用大cap, 后续正常)
        reground_lock: dict[str, int] = {}  # eid → 锁定到帧, 期间 vis_raw 不覆盖 center
        stale_cotracker: set[str] = set()   # 搬迁后旧查询点已留在背景，永久停用
        moving_obstacles: set[str] = set()
        verified_moving_obstacles: set[str] = set()
        obstacle_motion_count: dict[str, int] = {}
        alt_location_pending: dict[str, tuple[np.ndarray, int]] = {}
        tracklet_version: dict[str, int] = {eid: 0 for eid in owner}
        driven_count: dict[str, int] = {eid: 0 for eid in owner}
        tracker_motion_count: dict[str, int] = {eid: 0 for eid in owner}
        driven_arm: dict[str, str] = {}
        driven_last_frame: dict[str, int] = {}
        prev_target_centers: dict[str, np.ndarray] = {}
        prev_entity_centers: dict[str, np.ndarray] = {}
        prev_ee_cams: dict[str, np.ndarray] = {}
        sam_contact_last: dict[tuple[str, str], int] = {}
        review_candidate_last: dict[tuple[str, str], int] = {}
        pair_gap_history: dict[tuple[str, str], list[tuple[int, float]]] = {}
        aux_gap_history: dict[str, list[tuple[int, float]]] = {}
        aux_noncontact_streak: dict[str, int] = {}

        def _restart_tracklet(eid: str, frame_idx: int,
                              mask: np.ndarray | None) -> bool:
            """在重定位 mask 内重新查询 CoTracker，替换该实体后续旧轨迹。"""
            if (mask is None or ep.timestamps[frame_idx] - ep.timestamps[0] < 5.0
                    or graph.entities[eid].category != "A_target"):
                return False
            ids = np.asarray(graph.entities[eid].tracker_ids)
            pts = sample_mask_points(
                mask.astype(bool), n=len(ids),
                seed=(abs(hash(eid)) + frame_idx) % 2**31)
            if len(pts) != len(ids):
                return False
            self._log(f"  [f{frame_idx}] 重建 tracklet {eid}: "
                      f"v{tracklet_version[eid]}→v{tracklet_version[eid]+1}")
            # Re-query only the remaining suffix. Besides avoiding redundant
            # work, this keeps long-video chunking anchored at local frame 0.
            tr_new, vi_new = tracker3d.track(ep.rgb[frame_idx:], pts,
                                             query_frame=0)
            tracks[frame_idx:, ids] = tr_new
            visibs[frame_idx:, ids] = vi_new
            queries[ids] = pts
            tracklet_version[eid] += 1
            self.track_transitions.append({
                "frame": frame_idx, "eid": eid, "status": "tracking",
                "reason": "tracklet_restart",
                "tracklet_version": tracklet_version[eid]})
            stale_cotracker.discard(eid)
            visual_baselines.pop(eid, None)
            return True
        track_status: dict[str, str] = {eid: "tracking" for eid in owner}

        for f in range(0, ep.n_frames, self.stride):
            t_rel = ep.timestamps[f] - ep.timestamps[0]
            ellipsoids: dict[str, Ellipsoid] = {}
            claimed_masks: list = []  # 本帧已被认领的 SAM3 mask (互斥防串实例)
            deferred_reid_locations: dict[str, tuple] = {}

            def _other_tgts(exclude_eid: str) -> list:
                """其他 A_target 的 (center, r_max) 列表, 用于 _reground 互斥."""
                return [(oeid, centers[oeid],
                         float(graph.entities[oeid].ellipsoid.radii.max()))
                        for oeid in graph.entities
                        if oeid != exclude_eid
                        and graph.entities[oeid].category == "A_target"
                        and oeid in centers]

            def _apply_confirmed_location(_ent, _center: np.ndarray,
                                          _mask: np.ndarray,
                                          _reason: str) -> None:
                """延迟写回，避免备选中心污染同一帧其他实例的互斥判断。"""
                _prev = alt_location_pending.get(_ent.eid)
                if _prev is not None and np.linalg.norm(_center - _prev[0]) <= 0.08:
                    _count = _prev[1] + 1
                else:
                    _count = 1
                alt_location_pending[_ent.eid] = (_center.copy(), _count)
                if _count < 3:
                    return
                deferred_reid_locations[_ent.eid] = (
                    _ent, _center.copy(), _mask.copy(), _reason)

            # 两臂 TCP + 腕旋转预计算 (bonded 刚体跟随 + 夹爪循环共用)
            ee_cams, Rs_cam = {}, {}
            for arm in self.grippers:
                _st = ep.arm_state_at(f, arm)
                _T = ep.T_arm2cam[arm]
                ee_cams[arm] = _T[:3, :3] @ _st["ee"][:3] + _T[:3, 3]
                Rs_cam[arm] = _T[:3, :3] @ _quat_rotmat(_st["ee"][3:7])
            # —— 视觉跟踪实体 (target/obstacle): CoTracker 驱动 center; axes/radii 冻结 ——
            vis_raw: dict[str, np.ndarray] = {}    # 本帧 fresh 视觉中心
            vis_accepted: dict[str, bool] = {}     # 本帧视觉中心是否"被接受"(非冻结)
            for eid in owner:
                ent = graph.entities[eid]
                ids = np.asarray(ent.tracker_ids)
                # bonded 实体走 force_moving: 被夹持, 视觉直接接受 (跳过静态先验)
                # C_obstacle 遮挡时冻结 (防椭球偏移); A_target 不冻结 (需跟随夹爪)
                _fm = hold_anchor.get(eid) is not None
                _freeze = (ent.category == "C_obstacle")
                if eid in stale_cotracker and not _fm:
                    upd = {"center": None, "lost": True,
                           "accepted": False, "occluded": True}
                else:
                    upd = trk.update(eid, f, tracks[f, ids], visibs[f, ids],
                                     force_moving=_fm,
                                     freeze_on_occlusion=_freeze)
                if upd["center"] is not None and not upd.get("lost", False):
                    _candidate = upd["center"]
                    _identity_conflict = track_status[eid] in {
                        "occluded", "reidentifying"}
                    if ent.category == "C_obstacle" and not _fm:
                        _uv_delta = tracks[f, ids] - queries[ids]
                        _vis_keep = visibs[f, ids] > 0.5
                        _px_move = (float(np.linalg.norm(
                            np.median(_uv_delta[_vis_keep], axis=0)))
                            if np.count_nonzero(_vis_keep) >= 12 else 0.0)
                        _rigid = _coherent_track_motion(
                            queries[ids], tracks[f, ids], visibs[f, ids])
                        if eid not in moving_obstacles:
                            if _rigid and _px_move >= 12.0:
                                obstacle_motion_count[eid] = (
                                    obstacle_motion_count.get(eid, 0) + 1)
                            else:
                                obstacle_motion_count[eid] = 0
                            if obstacle_motion_count[eid] >= 3:
                                moving_obstacles.add(eid)
                                self._log(f"  [f{f}] 障碍物真实移动确认 {eid}: "
                                          f"二维位移 {_px_move:.1f}px")
                        # 点群运动只能说明“这个杯子原位置上的点动了”，不能
                        # 证明仍是同一杯实例。SAM3 在候选 mask 内复核前冻结 3D
                        # 中心，防止 cup1/2/3 之间串实例后污染后续碰撞。
                        if eid not in verified_moving_obstacles:
                            _candidate = centers_init[eid].copy()
                    if ent.category == "A_target" and not _fm:
                        _baseline = visual_baselines.get(eid)
                        if _baseline is None:
                            visual_baselines[eid] = _candidate.copy()
                            _candidate = trusted_centers[eid].copy()
                        else:
                            _motion = _candidate - _baseline
                            _move = float(np.linalg.norm(_motion))
                            _coherent = _coherent_track_motion(
                                queries[ids], tracks[f, ids], visibs[f, ids])
                            # CoTracker+单目深度在该数据上的短时抖动可达约 6cm；
                            # 10cm 仅是“明显已离开原实例”的遮挡闸门。真正的
                            # 新位置仍必须通过下方 SAM3+ReID 的 5cm/3次确认。
                            if _move <= 0.10 or _coherent:
                                _candidate = trusted_centers[eid] + _motion
                        if (_baseline is not None and _move > 0.10
                                and not _coherent):
                            _identity_conflict = True
                            if track_status[eid] not in {"occluded", "reidentifying"}:
                                self._log(f"  [f{f}] 冻结 {eid}: 未经 ReID 确认的相对移动 "
                                          f"{_move*100:.1f}cm")
                        for _oeid, _oc, _orr in _other_tgts(eid):
                            if _identity_candidate_conflict(
                                    eid, _oeid, _candidate, _oc, _orr):
                                _identity_conflict = True
                                self._log(
                                    f"  [f{f}] 冻结 {eid}: CoTracker 候选与 "
                                    f"{_oeid} 身份冲突")
                                break
                    if not _identity_conflict:
                        vis_raw[eid] = _candidate
                        vis_accepted[eid] = bool(upd.get("accepted", False))
                        lost_frames[eid] = 0
                        last_visual[eid] = f
                        track_status[eid] = "tracking"
                    # 身份冲突视作遮挡而不是移动：中心和 lost_frames 都不变。
                    # 这样物体仍留在最后可信位置，只有 SAM3 给出不冲突的新位置
                    # 时才会重定位。
                    else:
                        lost_frames[eid] += 1
                        if track_status[eid] != "occluded":
                            track_status[eid] = "occluded"
                            self.track_transitions.append({
                                "frame": f, "eid": eid, "status": "occluded",
                                "reason": "identity_conflict"})
                elif upd.get("occluded", False):
                    lost_frames[eid] += 1
                    if ent.category == "A_target":
                        track_status[eid] = "occluded"
                else:
                    lost_frames[eid] += 1
                    if ent.category == "A_target":
                        track_status[eid] = "occluded"

            # —— 中心决策: 持有时本体感知刚体跟随 (平移+旋转); 否则视觉/冻结 ——
            # axes/radii 冻结在帧 0 (碰撞体积不变); bonded 时 axes 随腕旋转 ΔR
            axes_dyn: dict[str, np.ndarray] = {}
            for eid in owner:
                anchor = hold_anchor.get(eid)
                if anchor is not None:
                    a_arm, c0, ee0, R0 = anchor
                    dR = Rs_cam[a_arm] @ R0.T
                    centers[eid] = ee_cams[a_arm] + dR @ (c0 - ee0)
                    axes_dyn[eid] = dR @ axes[eid]
                elif eid in vis_raw and f < reground_lock.get(eid, 0):
                    pass  # 重锚锁定: CoTracker 旧点在别处, 不覆盖 SAM3 新位置
                elif eid in vis_raw:
                    centers[eid] = vis_raw[eid]
                # 丢失且未持有 → 冻结 (centers[eid] 不变)
                # 连续丢失后旧位置只用于重定位参考，不再参与碰撞。
                # 否则人把物体搬走后，冻结椭球会在旧位置持续误报。
                if eid in centers:
                    ellipsoids[eid] = Ellipsoid(centers[eid], axes_dyn.get(eid, axes[eid]),
                                                radii[eid], padding=1.0)
            # —— 本体感知夹爪 (呼吸椭球) ——
            for arm, gm in self.grippers.items():
                st = ep.arm_state_at(f, arm)
                ee_cam = ee_cams[arm]
                R_cam_arm = Rs_cam[arm]
                quat_cam = _rotmat_quat(R_cam_arm)
                g_ell = gm.ellipsoid_at(ee_cam, quat_cam, st["gripper_width_m"])
                if st["gripper"] > GRASP_CLOSE_TH:
                    opened_since_grasp[arm] = True
                # 抓取绑定检测: 闭合 & TCP 贴目标 & 之前张开过 → 需视觉确认才 bonded
                # 状态按 (臂, 目标) 对维护, 一只臂同时只"持有"最先接触的目标
                held = next((t for t in graph.targets()
                             if t.bonded_to == f"gripper:{arm}"), None)

                def _bind(tgt, center, note: str):
                    """建立绑定: 锚=(臂, 中心, ee, 腕旋转R0), 供刚体跟随。"""
                    gm.holding = True
                    tgt.bonded_to = f"gripper:{arm}"
                    last_visual[tgt.eid] = f
                    reground_last[tgt.eid] = f
                    opened_since_grasp[arm] = False
                    hold_anchor[tgt.eid] = (arm, center.copy(), ee_cam.copy(),
                                            R_cam_arm.copy())
                    track_status[tgt.eid] = "bonded"
                    trusted_centers[tgt.eid] = center.copy()
                    centers[tgt.eid] = center
                    ellipsoids[tgt.eid] = Ellipsoid(center, axes[tgt.eid],
                                                    radii[tgt.eid], padding=1.0)
                    self.engine.events.append(CollisionEvent(
                        f, t_rel, "grasp", f"gripper:{arm}", tgt.eid,
                        {"gripper_width": round(st["gripper"], 4)},
                        f"臂[{arm}]闭合(gripper={st['gripper']:.3f}cm)接触目标"
                        f"[{tgt.name}], 建立抓取绑定{note}"))
                    self._log(f"  [f{f} {t_rel:.1f}s] {arm} 抓取绑定 → {tgt.eid} "
                              f"(target中心 {np.round(center, 3)}){note}")

                # —— 确认窗口: 抓取触发但无视觉确认 → SAM3 重查; 超时 → 放弃绑定 ——
                pend = pending.get(arm)
                if pend is not None:
                    tgt_p = pend["tgt"]
                    if st["gripper"] > GRASP_CLOSE_TH or tgt_p.bonded_to is not None:
                        pending.pop(arm, None)   # 夹爪已张开/目标已被绑 → 放弃
                    elif t_rel > pend["until_sec"]:
                        pending.pop(arm, None)
                        self.engine.events.append(CollisionEvent(
                            f, t_rel, "grasp_unverified", f"gripper:{arm}", tgt_p.eid,
                            {"window_s": 1.5,
                             "motion_state": "unverified_grasp_contact",
                             "collision_valid": False,
                             "needs_review": True,
                             "visibility": "occluded"},
                            f"臂[{arm}]闭合贴近目标[{tgt_p.name}]但1.5s内无视觉确认"
                            f"(全遮挡/抓空), 放弃绑定"))
                        # 遮挡时不能声称几何碰撞成立，但需要把同一时刻送给
                        # VLM，并明确降低先验置信度。选择当前最近的杯子只作为
                        # 裁决 ROI，不作为接触结论。
                        _near_ob = None
                        if tgt_p.eid in ellipsoids:
                            _near = [(float(np.linalg.norm(
                                        ellipsoids[tgt_p.eid].center
                                        - ellipsoids[_ob.eid].center)), _ob)
                                     for _ob in graph.obstacles()
                                     if _ob.eid in ellipsoids]
                            if _near:
                                _near_ob = min(_near, key=lambda x: x[0])
                        if _near_ob is not None:
                            self.engine._fire(CollisionEvent(
                                f, t_rel, "intersection", tgt_p.eid,
                                _near_ob[1].eid,
                                {"motion_state": "occluded_contact_candidate",
                                 "collision_valid": False,
                                 "needs_review": True,
                                 "visibility": "occluded",
                                 "center_gap": round(_near_ob[0], 3)},
                                f"目标[{tgt_p.name}]在夹爪闭合操作期间被完全遮挡；"
                                f"无法可靠确认其是否接触[{_near_ob[1].name}]，"
                                "请 VLM 结合接触反应复核，先验置信度应降低"))
                        self._log(f"  [f{f} {t_rel:.1f}s] {arm} 抓取未确认 "
                                  f"{tgt_p.eid} → 放弃绑定 (grasp_unverified)")
                    elif f >= pend["next"]:
                        pend["next"] = f + 5
                        pend["stable"] = pend.get("stable", 0) + 1
                        _confirm_r_p = float(radii[tgt_p.eid].max()) + 0.06
                        _rm = self._reground(tgt_p, f, ref_center=ee_cam,
                                              claimed_masks=claimed_masks,
                                              other_targets=_other_tgts(tgt_p.eid),
                                              return_mask=True)
                        c_re, mask_re = _rm if _rm is not None else (None, None)
                        _confirmed_tgt = None
                        _confirmed_c = None
                        if c_re is not None:
                            if self._tcp_in_mask(ee_cam, mask_re, near_px=50):
                                _confirmed_tgt, _confirmed_c = tgt_p, c_re
                            else:
                                # 距离够近但 TCP 不在 mask 上 → 可能抓错对象
                                self._log(f"  [f{f}] TCP 不在 {tgt_p.eid} mask, "
                                          f"尝试其他 A_target")
                                # ReID 已经完成多帧确认；TCP 不在 mask 仅能否定
                                # “被抓住”，不能否定实例的新位置。
                                _old = centers.get(tgt_p.eid)
                                centers[tgt_p.eid] = c_re.copy()
                                trusted_centers[tgt_p.eid] = c_re.copy()
                                visual_baselines.pop(tgt_p.eid, None)
                                trk.acknowledge_reground(tgt_p.eid, c_re)
                                if (_old is not None and
                                        np.linalg.norm(c_re - _old) > 0.10):
                                    stale_cotracker.add(tgt_p.eid)
                                track_status[tgt_p.eid] = "tracking"
                                ellipsoids[tgt_p.eid] = Ellipsoid(
                                    c_re, axes[tgt_p.eid], radii[tgt_p.eid],
                                    padding=1.0)
                                self._log(f"  [f{f}] ReID只重定位(不绑定) "
                                          f"{tgt_p.eid} → c={np.round(c_re, 3)}")
                                for _alt in graph.targets():
                                    if _alt.eid == tgt_p.eid or \
                                            _alt.bonded_to is not None or \
                                            _alt.category != "A_target":
                                        continue
                                    _rm2 = self._reground(
                                        _alt, f, ref_center=ee_cam,
                                        max_dist=1.0,
                                        return_mask=True)
                                    if _rm2 is None:
                                        continue
                                    _c2, _m2 = _rm2
                                    _apply_confirmed_location(
                                        _alt, _c2, _m2,
                                        "抓取备选，不代表绑定")
                                    _near3d = np.linalg.norm(_c2 - ee_cam) < 0.08
                                    if (self._tcp_in_mask(ee_cam, _m2, near_px=50)
                                            or (self.appearance is not None and _near3d)):
                                        if (_confirmed_c is None or
                                                np.linalg.norm(_c2-ee_cam) <
                                                np.linalg.norm(_confirmed_c-ee_cam)):
                                            _confirmed_tgt, _confirmed_c = _alt, _c2
                                # TCP-in-mask 全失败 → 不确认, 继续等
                        # 主候选也可能因旧中心距离 cap 被拒绝；仍需全局检查
                        # 其他实例，否则一个漂移的 lid 会挡住真实 kettle 的绑定。
                        if _confirmed_tgt is None:
                            for _alt in graph.targets():
                                if (_alt.eid == tgt_p.eid
                                        or _alt.bonded_to is not None
                                        or _alt.category != "A_target"):
                                    continue
                                _rm2 = self._reground(
                                    _alt, f, ref_center=ee_cam,
                                    max_dist=1.0, return_mask=True)
                                if _rm2 is None:
                                    continue
                                _c2, _m2 = _rm2
                                _apply_confirmed_location(
                                    _alt, _c2, _m2,
                                    "抓取备选，不代表绑定")
                                _near3d = np.linalg.norm(_c2 - ee_cam) < 0.08
                                if (self._tcp_in_mask(ee_cam, _m2, near_px=50)
                                        or (self.appearance is not None and _near3d)):
                                    if (_confirmed_c is None or
                                            np.linalg.norm(_c2-ee_cam) <
                                            np.linalg.norm(_confirmed_c-ee_cam)):
                                        _confirmed_tgt, _confirmed_c = _alt, _c2
                        if _confirmed_tgt is not None:
                            pending.pop(arm, None)
                            trk.acknowledge_reground(
                                _confirmed_tgt.eid, _confirmed_c)
                            _note = ("(TCP-in-mask纠正)" if _confirmed_tgt != tgt_p
                                     else "(确认窗口内SAM3补确认)")
                            _bind(_confirmed_tgt, _confirmed_c, _note)
                            held = _confirmed_tgt
                        elif (pend.get("pregrasp_trusted", False)
                              and pend["stable"] >= 3
                              and not deferred_reid_locations):
                            # 夹爪闭合会立即遮住目标。若闭合前身份仍可信，且
                            # 连续三个检查周期都保持闭合，则以最后可信位置绑定；
                            # 这不是盲绑，后续仍由定期 SAM3+ReID 检查滑落。
                            pending.pop(arm, None)
                            _c_pre = centers[tgt_p.eid].copy()
                            trk.acknowledge_reground(tgt_p.eid, _c_pre)
                            _bind(tgt_p, _c_pre, "(闭合前身份连续确认)")
                            held = tgt_p

                # 抓取检测: 先收集所有触发的目标, 选最近的绑定 (防 teapot 挡住 lid)
                _triggering = []
                for tgt in graph.targets():
                    if tgt.eid not in ellipsoids:
                        continue
                    pair = (arm, tgt.eid)
                    grasp_now = (opened_since_grasp[arm] and
                                 detect_grasp(ee_cam, ellipsoids[tgt.eid], st["gripper"]))
                    was = bonded_prev.get(pair, False)
                    bonded_prev[pair] = grasp_now
                    if grasp_now and not was and held is None and arm not in pending:
                        _d = float(np.linalg.norm(ee_cam - ellipsoids[tgt.eid].center))
                        _triggering.append((_d, tgt))
                if _triggering:
                    _triggering.sort()
                    _, tgt = _triggering[0]
                    opened_since_grasp[arm] = False
                    _confirm_r = float(radii[tgt.eid].max()) + 0.06
                    # 小物体 (r_max<0.13, 如 lid): detect_grasp 已足够确认,
                    # 遮挡下无法视觉确认 → 直接盲绑 (用冻结中心做锚)
                    if (radii[tgt.eid].max() < 0.13
                            and self.appearance is None):
                        c_anchor = centers.get(tgt.eid, tgt.ellipsoid.center).copy()
                        trk.acknowledge_reground(tgt.eid, c_anchor)
                        _bind(tgt, c_anchor, "(小物体盲绑)")
                        blind_bound.add(tgt.eid)
                        held = tgt
                    else:
                        # 绑定确认: SAM3+TCP-in-mask 优先 (防抓错对象);
                        # 失败则回退 CoTracker 视觉中心
                        _ctgt, _cc = None, None
                        _rm = self._reground(tgt, f, ref_center=ee_cam,
                                              claimed_masks=claimed_masks,
                                              other_targets=_other_tgts(tgt.eid),
                                              return_mask=True)
                        c_re, mask_re = _rm if _rm is not None else (None, None)
                        if c_re is not None:
                            if self._tcp_in_mask(ee_cam, mask_re, near_px=50):
                                _ctgt, _cc = tgt, c_re
                            else:
                                self._log(f"  [f{f}] TCP 不在 {tgt.eid} mask, "
                                          f"尝试其他 A_target")
                                _old = centers.get(tgt.eid)
                                centers[tgt.eid] = c_re.copy()
                                trusted_centers[tgt.eid] = c_re.copy()
                                visual_baselines.pop(tgt.eid, None)
                                trk.acknowledge_reground(tgt.eid, c_re)
                                if (_old is not None and
                                        np.linalg.norm(c_re - _old) > 0.10):
                                    stale_cotracker.add(tgt.eid)
                                track_status[tgt.eid] = "tracking"
                                ellipsoids[tgt.eid] = Ellipsoid(
                                    c_re, axes[tgt.eid], radii[tgt.eid],
                                    padding=1.0)
                                self._log(f"  [f{f}] ReID只重定位(不绑定) "
                                          f"{tgt.eid} → c={np.round(c_re, 3)}")
                                for _alt in graph.targets():
                                    if _alt.eid == tgt.eid or \
                                            _alt.bonded_to is not None or \
                                            _alt.category != "A_target":
                                        continue
                                    _rm2 = self._reground(
                                        _alt, f, ref_center=ee_cam,
                                        max_dist=1.0,
                                        return_mask=True)
                                    if _rm2 is None:
                                        continue
                                    _c2, _m2 = _rm2
                                    _apply_confirmed_location(
                                        _alt, _c2, _m2,
                                        "抓取备选，不代表绑定")
                                    _near3d = np.linalg.norm(_c2 - ee_cam) < 0.08
                                    if (self._tcp_in_mask(ee_cam, _m2, near_px=50)
                                            or (self.appearance is not None and _near3d)):
                                        if (_cc is None or
                                                np.linalg.norm(_c2-ee_cam) <
                                                np.linalg.norm(_cc-ee_cam)):
                                            _ctgt, _cc = _alt, _c2
                                # TCP-in-mask 全失败 → 不确认, 进 pending 等真正接触
                        if _ctgt is None:
                            for _alt in graph.targets():
                                if (_alt.eid == tgt.eid
                                        or _alt.bonded_to is not None
                                        or _alt.category != "A_target"):
                                    continue
                                _rm2 = self._reground(
                                    _alt, f, ref_center=ee_cam,
                                    max_dist=1.0, return_mask=True)
                                if _rm2 is None:
                                    continue
                                _c2, _m2 = _rm2
                                _apply_confirmed_location(
                                    _alt, _c2, _m2,
                                    "抓取备选，不代表绑定")
                                _near3d = np.linalg.norm(_c2 - ee_cam) < 0.08
                                if (self._tcp_in_mask(ee_cam, _m2, near_px=50)
                                        or (self.appearance is not None and _near3d)):
                                    if (_cc is None or
                                            np.linalg.norm(_c2-ee_cam) <
                                            np.linalg.norm(_cc-ee_cam)):
                                        _ctgt, _cc = _alt, _c2
                        if _ctgt is not None:
                            # 不在闭合边沿单帧绑定；进入确认窗口可过滤短暂夹爪
                            # 抖动（本视频 f445→f450 即为典型假触发）。
                            pending[arm] = {
                                "tgt": _ctgt, "until_sec": float(t_rel + 1.5),
                                "next": f + 5, "stable": 0,
                                "pregrasp_trusted":
                                    track_status.get(_ctgt.eid) == "tracking"}
                        else:
                            pending[arm] = {"tgt": tgt, "until_sec": float(t_rel + 1.5),
                                            "next": f + 5, "stable": 0,
                                            "pregrasp_trusted":
                                                track_status.get(tgt.eid) == "tracking"}
                            self._log(f"  [f{f} {t_rel:.1f}s] {arm} 抓取触发但"
                                      f"无视觉确认 {tgt.eid} → 进入确认窗口")
                # 小物体补获: lid 的 CoTracker 可能在夹爪闭合前已经漂到
                # 另一个壶盖上，此时基于旧椭球中心的 detect_grasp 永远不会触发。
                # 在“张开过→刚闭合”边沿主动查找 TCP 附近的小 A_target mask。
                _closed_now = st["gripper"] <= GRASP_CLOSE_TH
                _just_closed = _closed_now and not close_prev[arm]
                if (_just_closed and opened_since_grasp[arm] and held is None
                        and arm not in pending and not _triggering):
                    _hits = []
                    for _small in graph.targets():
                        if (_small.bonded_to is not None
                                or _small.eid not in radii
                                or float(radii[_small.eid].max()) >= 0.13):
                            continue
                        _rm_small = self._reground(
                            _small, f, ref_center=ee_cam, max_dist=0.30,
                            claimed_masks=claimed_masks,
                            other_targets=_other_tgts(_small.eid),
                            return_mask=True)
                        if _rm_small is None:
                            continue
                        _c_small, _m_small = _rm_small
                        if self._tcp_in_mask(ee_cam, _m_small, near_px=60):
                            _hits.append((float(np.linalg.norm(_c_small - ee_cam)),
                                          _small, _c_small))
                    if _hits:
                        _, _small, _c_small = min(_hits, key=lambda x: x[0])
                        trk.acknowledge_reground(_small.eid, _c_small)
                        _bind(_small, _c_small, "(闭合边沿SAM3小物体补获)")
                        held = _small
                        opened_since_grasp[arm] = False
                close_prev[arm] = _closed_now
                # 滑落检测: 视觉被接受(非冻结)且可见时, 视觉中心 vs 本体跟随中心 偏离过大
                # (物体没跟着走 = 滑落/抓取失败)。冻结态不算; 盲绑实体不算 (无可靠视觉)
                if held is not None and held.eid in vis_raw and \
                        vis_accepted.get(held.eid, False) and \
                        held.eid not in blind_bound:
                    div = float(np.linalg.norm(
                        vis_raw[held.eid] - centers[held.eid]))
                    slip_th = float(held.ellipsoid.radii.max()) + 0.08
                    slip_cnt[arm] = slip_cnt.get(arm, 0) + 1 if div > slip_th else 0
                    if slip_cnt[arm] >= 4:  # 连续 4 帧确认
                        gm.holding = False
                        held.bonded_to = None
                        hold_anchor.pop(held.eid, None)
                        blind_bound.discard(held.eid)
                        slip_cnt[arm] = 0
                        centers[held.eid] = vis_raw[held.eid]  # 回到视觉定位
                        self._log(f"  [f{f} {t_rel:.1f}s] {arm} 滑落 {held.eid} "
                                  f"(视觉偏离 {div*100:.0f}cm > {slip_th*100:.0f}cm)")
                        self.engine.events.append(CollisionEvent(
                            f, t_rel, "slip", f"gripper:{arm}", held.eid,
                            {"divergence": round(div, 3)},
                            f"臂[{arm}]夹爪仍闭合但目标[{held.name}]未随臂运动 "
                            f"(偏离 {div*100:.0f}cm) —— 搬运滑落/抓取失败"))
                        held = None
                # SAM3 定期重接地确认: bonded 期间独立验证物体是否还在跟随位置。
                # CoTracker 可能冻结/跟错 (遮挡时返回旧位置), 夹爪传感器可能卡死
                # (如左臂 0.0004 不动) —— 不能只靠 vis_raw 或 gripper 判释放。
                if held is not None:
                    _lost = lost_frames.get(held.eid, 0)
                    # CoTracker 丢失 → 频繁检查 (每 ~1.3s); 有视觉 → 稀疏检查 (每 ~4s)
                    _interval = (10 * self.stride) if _lost > 15 else (30 * self.stride)
                    if f - reground_last.get(held.eid, 0) >= _interval:
                        reground_last[held.eid] = f
                        c_re = self._reground(held, f, centers.get(held.eid),
                                              claimed_masks=claimed_masks,
                                              other_targets=_other_tgts(held.eid))
                        if c_re is not None:
                            last_visual[held.eid] = f
                            div = float(np.linalg.norm(c_re - centers[held.eid]))
                            slip_th = float(held.ellipsoid.radii.max()) + 0.08
                            if div > slip_th:
                                gm.holding = False
                                held.bonded_to = None
                                hold_anchor.pop(held.eid, None)
                                blind_bound.discard(held.eid)
                                slip_cnt[arm] = 0
                                centers[held.eid] = c_re
                                trusted_centers[held.eid] = c_re.copy()
                                held.c_init = c_re.copy()
                                trk.acknowledge_reground(held.eid, c_re)
                                self._log(f"  [f{f} {t_rel:.1f}s] {arm} 滑落(重锚定) "
                                          f"{held.eid} (SAM3偏离 {div*100:.0f}cm > "
                                          f"{slip_th*100:.0f}cm, lost={_lost})")
                                self.engine.events.append(CollisionEvent(
                                    f, t_rel, "slip", f"gripper:{arm}", held.eid,
                                    {"divergence": round(div, 3),
                                     "method": "reground"},
                                    f"臂[{arm}]SAM3重锚定发现目标"
                                    f"[{held.name}]未随臂运动 (偏离{div*100:.0f}cm) "
                                    f"—— 抓取失败/滑落"))
                                held = None
                            else:
                                # 物体仍在跟随位置 → 校正漂移, 更新锚点(含腕旋转参考)
                                centers[held.eid] = c_re
                                trusted_centers[held.eid] = c_re.copy()
                                trk.acknowledge_reground(held.eid, c_re)
                                hold_anchor[held.eid] = (
                                    arm, c_re.copy(), ee_cam.copy(),
                                    R_cam_arm.copy())
                # 遮挡超时不能单独证明滑落。抓取物在壶体后方持续不可见是
                # 倒茶任务的常态；若此处自动解绑，恰好会丢掉遮挡期间由机器人
                # 本体位姿提供的碰撞代理。超过 10s 后只降低可观测性，继续保持
                # 刚体绑定，直到夹爪张开或 SAM/ReID 在别处确认了该实例。
                if held is not None:
                    _blind = float(ep.timestamps[f] - ep.timestamps[last_visual.get(held.eid, f)])
                    # 盲绑实体跳过超时 (bond 基于夹爪闭合, 释放检测会处理夹爪张开);
                    # 仅视觉确认的 bond 才用超时 (传感器可能卡死)
                    if _blind > 10.0 and held.eid not in blind_bound:
                        blind_bound.add(held.eid)
                        self._log(f"  [f{f} {t_rel:.1f}s] {arm} 进入遮挡绑定 "
                                  f"{held.eid} (无视觉确认 {_blind:.1f}s，"
                                  "保留刚体代理但降低碰撞先验)")
                # 释放: 持有物 且 夹爪张开 → 解绑 + 立即 SAM3 重锚 (物体可能被放新位置)
                if held is not None and st["gripper"] > GRASP_CLOSE_TH:
                    _released = held
                    gm.holding = False
                    _released.bonded_to = None
                    hold_anchor.pop(_released.eid, None)
                    blind_bound.discard(_released.eid)
                    released_at[_released.eid] = f
                    slip_cnt[arm] = 0
                    # 释放即重锚: 免冷却, 放宽 cap (物体可能被放在远处)
                    c_re = self._reground(_released, f, centers.get(_released.eid),
                                          claimed_masks=claimed_masks, max_dist=1.0,
                                          other_targets=_other_tgts(_released.eid))
                    if c_re is not None:
                        trk.acknowledge_reground(_released.eid, c_re)
                        centers[_released.eid] = c_re
                        trusted_centers[_released.eid] = c_re.copy()
                        _released.c_init = c_re.copy()
                        lost_frames[_released.eid] = 0
                        last_visual[_released.eid] = f
                        reground_last[_released.eid] = f
                        released_at.pop(_released.eid, None)
                        reground_lock[_released.eid] = f + 30 * self.stride
                        ellipsoids[_released.eid] = Ellipsoid(
                            c_re, axes[_released.eid], radii[_released.eid],
                            padding=1.0)
                        self._log(f"  [f{f}] 释放重锚 {_released.eid} → "
                                  f"c={np.round(c_re, 3)}")
                    elif _released.eid in vis_raw:
                        centers[_released.eid] = vis_raw[_released.eid]
                    self._log(f"  [f{f} {t_rel:.1f}s] {arm} 释放 {_released.eid}")
                    self.engine.events.append(CollisionEvent(
                        f, t_rel, "release", f"gripper:{arm}", _released.eid,
                        {"gripper_width": round(st["gripper"], 4)},
                        f"臂[{arm}]张开(gripper={st['gripper']:.3f}cm), "
                        f"释放目标[{_released.name}]"))
                    held = None
                ee_prev[arm] = ee_cam
                ellipsoids[f"gripper:{arm}"] = g_ell
            # 所有夹爪与候选已完成本帧竞争后，再统一提交各自 ReID 位置。
            for _deid, (_dent, _dc, _dm, _why) in deferred_reid_locations.items():
                if _dent.bonded_to is not None:
                    continue
                _old = centers.get(_deid)
                centers[_deid] = _dc.copy()
                _dent.mask = _dm.copy()
                trusted_centers[_deid] = _dc.copy()
                visual_baselines.pop(_deid, None)
                trk.acknowledge_reground(_deid, _dc)
                _ids = np.asarray(_dent.tracker_ids)
                _uv = np.rint(tracks[f, _ids]).astype(int)
                _h, _w = _dm.shape[:2]
                _inside = ((_uv[:, 0] >= 0) & (_uv[:, 0] < _w) &
                           (_uv[:, 1] >= 0) & (_uv[:, 1] < _h) &
                           (visibs[f, _ids] > 0.5))
                _mask_overlap = 0.0
                if np.any(_inside):
                    _mask_overlap = float(np.mean(
                        _dm[_uv[_inside, 1], _uv[_inside, 0]] > 0))
                _distance = (float(np.linalg.norm(_dc - _old))
                             if _old is not None else 0.0)
                _need_new_tracklet = (
                    tracklet_version.get(_deid, 0) == 0
                    or _distance > 0.10 or _mask_overlap < 0.5)
                if _need_new_tracklet:
                    if not _restart_tracklet(_deid, f, _dm):
                        stale_cotracker.add(_deid)
                track_status[_deid] = "tracking"
                ellipsoids[_deid] = Ellipsoid(
                    _dc, axes[_deid], radii[_deid], padding=1.0)
                self.engine.reset_support_baseline(
                    _deid, ellipsoids[_deid], graph)
                if _old is None or np.linalg.norm(_dc - _old) > 0.02:
                    self._log(f"  [f{f}] ReID定位 {_deid}({_why}) → "
                              f"c={np.round(_dc, 3)}")
            # —— 非bonded 实体: SAM3 周期重锚定恢复 ——
            # 释放后/遮挡后物体可能在新位置; CoTracker 重入门控会拒绝远跳,
            # 必须靠 SAM3 重新确认才能让椭球回到视觉位置 (否则永久冻结在陈旧位)
            # A_target 额外: 即使未 lost 也定期验证 (CoTracker 可能跟错物体)
            for eid in owner:
                if hold_anchor.get(eid) is not None:
                    continue   # bonded → 已有 bonded 重锚逻辑
                _ent_chk = graph.entities[eid]
                _is_lost = lost_frames.get(eid, 0) > 6
                _is_atarget_periodic = (_ent_chk.category == "A_target"
                    and f - reground_last.get(eid, 0) >= 30 * self.stride)
                _is_c_pending_verify = (
                    _ent_chk.category == "C_obstacle"
                    and eid in moving_obstacles
                    and eid not in verified_moving_obstacles)
                if not _is_lost and not _is_atarget_periodic and not _is_c_pending_verify:
                    continue
                _reid_fast = track_status.get(eid) == "reidentifying"
                _cooldown = self.stride if _reid_fast else 10 * self.stride
                if f - reground_last.get(eid, 0) < _cooldown:
                    continue   # 冷却中 (~1s)
                if _ent_chk.category == "C_obstacle":
                    # 静态障碍物在二维真实移动被确认前，不允许深度噪声触发
                    # SAM 重锚或 tracklet 重建。
                    if eid not in moving_obstacles:
                        continue
                    _ids = np.asarray(_ent_chk.tracker_ids)
                    if len(_ids) > 0:
                        _vf = float(np.mean(visibs[f, _ids] > 0.5))
                        if _vf < 0.8:
                            continue
                reground_last[eid] = f
                ent = graph.entities[eid]
                # 释放后放宽 cap; A_target 首次重锚也放宽 (ref 可能是 stale init)
                # 后续重锚恢复正常 cap (ref 已更新, 防假阳性跳来跳去)
                # A_target 定期验证 (非 lost): 中等 cap (catch 跟错 CoTracker)
                _rl = f - released_at.get(eid, -9999)
                if _rl < 30 * self.stride:
                    _md = 1.0
                elif ent.category == "A_target" and eid not in regrounded_once:
                    _md = 0.5
                elif ent.category == "A_target" and not _is_lost:
                    _md = 0.35
                elif ent.category == "A_target" and _is_lost:
                    # 长丢失 cap 升级: 物体被搬走可能离冻结中心很远
                    _lf = lost_frames.get(eid, 0)
                    if _lf > 180:
                        _md = 0.60
                    elif _lf > 120:
                        _md = 0.50
                    elif _lf > 60:
                        _md = 0.40
                    else:
                        _md = None
                else:
                    _md = None
                # 人手遮挡并将目标搬到远处后，旧中心不应再作为
                # 硬距离 cap。丢失约 2s 后进入全局重定位。
                _allow_global = (ent.category == "A_target" and _is_lost
                                 and lost_frames.get(eid, 0) > 12)
                _assoc_center = None
                if _allow_global and eid.lower().endswith("_lid"):
                    _owner = eid.lower().rsplit("_lid", 1)[0].split(":")[-1]
                    for _oeid, _oent in graph.entities.items():
                        if (_oent.category == "A_target" and _oeid != eid
                                and _oent.name.lower().replace(" ", "_") == _owner
                                and _oeid in centers
                                and lost_frames.get(_oeid, 0) <= 6):
                            _assoc_center = centers[_oeid]
                            break
                _rr = self._reground(ent, f, centers.get(eid),
                                     claimed_masks=claimed_masks,
                                     max_dist=_md,
                                     other_targets=_other_tgts(eid),
                                     allow_global=_allow_global,
                                     association_center=_assoc_center,
                                     track_uv=tracks[f, np.asarray(ent.tracker_ids)],
                                     track_visibility=visibs[f, np.asarray(ent.tracker_ids)],
                                     return_mask=True)
                c_re, mask_re = _rr if _rr is not None else (None, None)
                if c_re is not None:
                    _old_center = centers.get(eid)
                    trk.acknowledge_reground(eid, c_re)
                    centers[eid] = c_re
                    ent.mask = mask_re.copy()
                    trusted_centers[eid] = c_re.copy()
                    # SAM3+ReID 已建立新的绝对位置；后续 CoTracker 第一次出现时
                    # 重新吸收其固定坐标偏差，避免立即把实体拉回旧轨迹。
                    visual_baselines.pop(eid, None)
                    if (_old_center is not None and
                            np.linalg.norm(c_re - _old_center) > 0.10):
                        if (t_rel >= 5.0 and ent.category == "A_target"
                                and not _restart_tracklet(eid, f, mask_re)):
                            stale_cotracker.add(eid)
                    ent.c_init = c_re.copy()
                    lost_frames[eid] = 0
                    track_status[eid] = "tracking"
                    if ent.category == "C_obstacle":
                        verified_moving_obstacles.add(eid)
                    last_visual[eid] = f
                    released_at.pop(eid, None)
                    regrounded_once.add(eid)
                    reground_lock[eid] = f + 30 * self.stride
                    ellipsoids[eid] = Ellipsoid(c_re, axes[eid],
                                                radii[eid], padding=1.0)
                    self.engine.reset_support_baseline(
                        eid, ellipsoids[eid], graph)
                    _mode = "全局搬迁恢复" if _allow_global else "恢复"
                    self._log(f"  [f{f}] SAM3 重锚定({_mode}) {eid} → "
                              f"c={np.round(c_re, 3)}")
                elif (self.appearance is not None
                      and eid in self.appearance.pending):
                    track_status[eid] = "reidentifying"
            # —— 运动学驱动状态 ——
            # 抓取视觉确认或夹爪开合遥测在遮挡/标定偏差下可能失败。
            # 若任务物体与机械臂 TCP 连续
            # 同向、同幅运动且相对位置稳定，标记 DRIVEN，使其仍作为
            # 碰撞主动体；这不是 BONDED，不用于刚体位姿覆盖。
            driven_targets: set[str] = set()
            for eid, ent in tracked.items():
                if (ent.category != "A_target" or eid not in centers
                        or track_status.get(eid) not in {"tracking", "bonded", "driven"}):
                    continue
                if ent.bonded_to is not None:
                    driven_targets.add(eid)
                    continue
                c_prev = prev_target_centers.get(eid)
                best_arm = None
                best_score = -1.0
                if c_prev is not None:
                    dc = centers[eid] - c_prev
                    nc = float(np.linalg.norm(dc))
                    for arm, ee_now in ee_cams.items():
                        ee_old = prev_ee_cams.get(arm)
                        if ee_old is None:
                            continue
                        de = ee_now - ee_old
                        ne = float(np.linalg.norm(de))
                        # stride=5 时相邻采样仅约 0.17s，正常端持移动常低于
                        # 5mm；原阈值在九分钟回归中从未激活 DRIVEN。
                        if nc < 0.001 or ne < 0.001:
                            continue
                        cosine = float(np.dot(dc, de) / max(nc * ne, 1e-9))
                        motion_error = float(np.linalg.norm(dc - de))
                        rel_stable = abs(
                            np.linalg.norm(centers[eid] - ee_now)
                            - np.linalg.norm(c_prev - ee_old)) <= 0.04
                        target_near_arm = (
                            np.linalg.norm(centers[eid] - ee_now)
                            <= float(np.max(radii[eid])) + 0.55)
                        if (cosine >= 0.40 and motion_error <= 0.04
                                and rel_stable and target_near_arm):
                            score = cosine - motion_error
                            if score > best_score:
                                best_score, best_arm = score, arm
                # 视觉主证据：目标内部点群连续呈刚性二维运动。TCP 外参或
                # 开合遥测不可靠时，仍应让真实运动的壶成为碰撞主动体。
                # 使用相邻检测帧而非首帧累计位移，避免物体放下后永久激活。
                ids = np.asarray(ent.tracker_ids)
                prev_f = max(0, f - self.stride)
                step_vis = np.minimum(visibs[f, ids], visibs[prev_f, ids])
                step_keep = step_vis > 0.5
                step_px = (float(np.linalg.norm(np.median(
                    tracks[f, ids][step_keep] - tracks[prev_f, ids][step_keep],
                    axis=0))) if np.count_nonzero(step_keep) >= 12 else 0.0)
                rigid_step = _coherent_track_motion(
                    tracks[prev_f, ids], tracks[f, ids], step_vis)
                if rigid_step and step_px >= 0.75:
                    tracker_motion_count[eid] = min(
                        5, tracker_motion_count.get(eid, 0) + 1)
                else:
                    tracker_motion_count[eid] = max(
                        0, tracker_motion_count.get(eid, 0) - 1)
                if best_arm is None and tracker_motion_count[eid] >= 2:
                    nearest_arm, nearest_dist = min(
                        ((arm, float(np.linalg.norm(centers[eid] - ee)))
                         for arm, ee in ee_cams.items()), key=lambda item: item[1])
                    if nearest_dist <= float(np.max(radii[eid])) + 0.70:
                        best_arm = nearest_arm
                if best_arm is not None:
                    if driven_arm.get(eid) == best_arm:
                        driven_count[eid] = driven_count.get(eid, 0) + 1
                    else:
                        driven_arm[eid] = best_arm
                        driven_count[eid] = 1
                else:
                    driven_count[eid] = max(0, driven_count.get(eid, 0) - 1)
                    if driven_count[eid] == 0:
                        driven_arm.pop(eid, None)
                if driven_count.get(eid, 0) >= 3:
                    driven_targets.add(eid)
                    driven_last_frame[eid] = f
                    track_status[eid] = "driven"
                    if driven_count[eid] == 3:
                        self.track_transitions.append({
                            "frame": f, "eid": eid, "status": "driven",
                            "reason": "gripper_motion_correlation",
                            "arm": driven_arm[eid]})
                        self._log(f"  [f{f}] 运动学驱动确认 {eid} ← {driven_arm[eid]}")
                        if "lid" in ent.name.lower():
                            # lid 已出现与机械臂一致的独立运动，但硬 3D/TCP-mask
                            # 接触可能因细小目标标定误差失败。生成低置信复核候选，
                            # 不直接视作有效碰撞。
                            _review_pair = (f"gripper:{driven_arm[eid]}", eid)
                            if (t_rel - review_candidate_last.get(
                                    _review_pair, -1e9) < 10.0):
                                continue
                            review_candidate_last[_review_pair] = float(t_rel)
                            _lid_box = _visible_track_box(
                                tracks[f, np.asarray(ent.tracker_ids)],
                                visibs[f, np.asarray(ent.tracker_ids)])
                            _roi = None
                            if _lid_box is not None:
                                _tcp = ep.project_to_pixels(
                                    ee_cams[driven_arm[eid]].reshape(1, 3))[0]
                                _roi = [max(0.0, min(_lid_box[0], _tcp[0]) - 50),
                                        max(0.0, min(_lid_box[1], _tcp[1]) - 50),
                                        min(ep.rgb.shape[2], max(_lid_box[2], _tcp[0]) + 50),
                                        min(ep.rgb.shape[1], max(_lid_box[3], _tcp[1]) + 50)]
                            self.engine._fire(CollisionEvent(
                                f, t_rel, "intersection",
                                f"gripper:{driven_arm[eid]}", eid,
                                {"motion_state": "lid_motion_contact_candidate",
                                 "collision_valid": False,
                                 "needs_review": True,
                                 "tracker_motion_px": round(step_px, 2),
                                 **({"roi_xyxy": [round(v, 1) for v in _roi],
                                     "view": "faceImg",
                                     "roi_includes_both": True} if _roi else {})},
                                f"壶盖[{ent.name}]与夹爪[{driven_arm[eid]}]运动相关，"
                                "但硬接触几何未确认；请 VLM 检查是否存在接触及"
                                "壶盖受撞反应"))

            # —— 二维接触补偿 ——
            # 3D 单目深度会让壶和杯在 z 方向错开。先用 CoTracker 轮廓生成
            # 低成本近接触候选，再只对候选帧运行 SAM3 精 mask 复核。
            track_boxes: dict[str, list[float]] = {}
            for _eid, _ent in tracked.items():
                _ids = np.asarray(_ent.tracker_ids)
                _box = _visible_track_box(
                    tracks[f, _ids], visibs[f, _ids])
                if _box is not None:
                    track_boxes[_eid] = _box
            if self.sam3 is not None:
                for _tid, _tent in tracked.items():
                    if (_tent.category != "A_target"
                            or _tid not in track_boxes
                            or track_status.get(_tid) not in {
                                "tracking", "bonded", "driven"}):
                        continue
                    _active_2d = (_tid in driven_targets
                                  or _tent.bonded_to is not None
                                  or tracker_motion_count.get(_tid, 0) >= 1)
                    if not _active_2d:
                        continue
                    for _ob in graph.obstacles():
                        if _ob.eid not in track_boxes:
                            continue
                        _pair = (_tid, _ob.eid)
                        _gap_now = _box_gap(
                            track_boxes[_tid], track_boxes[_ob.eid])
                        _history = pair_gap_history.setdefault(_pair, [])
                        _history.append((f, _gap_now))
                        del _history[:-12]  # stride=5 时约保留最近 2 秒
                        if f - sam_contact_last.get(_pair, -10**9) < 60:
                            continue
                        if _gap_now > 18:
                            continue
                        sam_contact_last[_pair] = f
                        _ta = self.sam3.segment(
                            ep.rgb[f], _tent.name, track_boxes[_tid])
                        _tb = self.sam3.segment(
                            ep.rgb[f], _ob.name, track_boxes[_ob.eid])
                        if (_ta["mask"] is None or _tb["mask"] is None
                                or min(_ta["score"], _tb["score"]) < 0.45
                                or not _masks_within(
                                    _ta["mask"], _tb["mask"], pixels=6)):
                            continue
                        _depth_evidence = _mask_depth_contact_evidence(
                            ep.depth[f], _ta["mask"], _tb["mask"])
                        _depth_evidence.update({
                            "depth_source": getattr(ep, "depth_source", "unknown"),
                            "depth_confidence": getattr(ep, "depth_confidence", 0.5),
                        })
                        _transition = _box_contact_transition(
                            _history, f, _gap_now, ep.timestamps)
                        _target_step = (0.0 if _tid not in prev_entity_centers
                                        else float(np.linalg.norm(
                                            centers[_tid] - prev_entity_centers[_tid])))
                        _object_step = (0.0 if _ob.eid not in prev_entity_centers
                                        else float(np.linalg.norm(
                                            centers[_ob.eid] - prev_entity_centers[_ob.eid])))
                        self.engine._fire(CollisionEvent(
                            f, t_rel, "intersection", _tid, _ob.eid,
                            {"motion_state": "sam3_2d_contact",
                             "track_box_gap_px": round(_gap_now, 1),
                             **_transition,
                             "target_step_m": round(_target_step, 4),
                             "obstacle_step_m": round(_object_step, 4),
                             "sam_scores": [round(_ta["score"], 3),
                                            round(_tb["score"], 3)],
                             **_depth_evidence},
                            f"活动实体[{_tent.name}]与障碍物[{_ob.name}]"
                            "经二维跟踪近接触及 SAM3 mask 复核确认接触"))

                # 夹爪—壶盖：用 TCP 投影与 SAM3 lid mask 复核，补偿粗夹爪
                # 椭球在 3D 中未相交的情况；仍要求 lid 点群正在运动。
                for _tid, _tent in tracked.items():
                    if ("lid" not in _tent.name.lower()
                            or _tid not in track_boxes
                            or tracker_motion_count.get(_tid, 0) < 1
                            or track_status.get(_tid) not in {
                                "tracking", "driven"}):
                        continue
                    _box = track_boxes[_tid]
                    for _arm, _ee in ee_cams.items():
                        _uv = ep.project_to_pixels(_ee.reshape(1, 3))[0]
                        if not (_box[0] - 35 <= _uv[0] <= _box[2] + 35
                                and _box[1] - 35 <= _uv[1] <= _box[3] + 35):
                            continue
                        _pair = (f"gripper:{_arm}", _tid)
                        if f - sam_contact_last.get(_pair, -10**9) < 60:
                            continue
                        sam_contact_last[_pair] = f
                        _lid = self.sam3.segment(ep.rgb[f], _tent.name, _box)
                        if (_lid["mask"] is None or _lid["score"] < 0.45
                                or not self._tcp_in_mask(
                                    _ee, _lid["mask"], near_px=25)):
                            continue
                        self.engine._fire(CollisionEvent(
                            f, t_rel, "intersection", f"gripper:{_arm}", _tid,
                            {"motion_state": "sam3_lid_contact",
                             "sam_score": round(_lid["score"], 3)},
                            f"夹爪[{_arm}]与壶盖[{_tent.name}]经 TCP 投影及 "
                            "SAM3 mask 复核确认接触并伴随壶盖运动"))

                # 夹爪—普通任务物体：三维深度错位时，用 TCP 投影到 SAM3
                # 实例 mask 的接触起始作为补偿。这里不把二维重叠直接判成
                # 碰撞；必须同时有相对接近/目标随动，并把局部边界深度交给
                # VLM 做最终的“正常抓取还是意外磕碰”裁决。
                for _tid, _tent in tracked.items():
                    if ("lid" in _tent.name.lower()
                            or _tent.bonded_to is not None
                            or _tid not in track_boxes
                            or track_status.get(_tid) not in {
                                "tracking", "driven"}):
                        continue
                    _motion_supported = (_tid in driven_targets
                        or tracker_motion_count.get(_tid, 0) >= 1)
                    if not _motion_supported:
                        continue
                    _box = track_boxes[_tid]
                    for _arm, _ee in ee_cams.items():
                        _uv = ep.project_to_pixels(_ee.reshape(1, 3))[0]
                        _pair = (f"gripper:{_arm}", _tid)
                        _gap_now = _point_box_gap(_uv, _box)
                        _history = pair_gap_history.setdefault(_pair, [])
                        _history.append((f, _gap_now))
                        del _history[:-18]
                        if (_gap_now > 40
                                or f - sam_contact_last.get(
                                    _pair, -10**9) < 60):
                            continue
                        sam_contact_last[_pair] = f
                        _target = self.sam3.segment(
                            ep.rgb[f], _tent.name, _box)
                        if (_target["mask"] is None
                                or _target["score"] < 0.45
                                or not self._tcp_in_mask(
                                    _ee, _target["mask"], near_px=30)):
                            continue
                        _tcp_mask = np.zeros_like(_target["mask"], dtype=np.uint8)
                        _u, _v = int(round(_uv[0])), int(round(_uv[1]))
                        if (0 <= _v < _tcp_mask.shape[0]
                                and 0 <= _u < _tcp_mask.shape[1]):
                            import cv2
                            cv2.circle(_tcp_mask, (_u, _v), 8, 1, -1)
                        _tcp_mask = _tcp_mask.astype(bool)
                        _depth_evidence = _mask_depth_contact_evidence(
                            ep.depth[f], _tcp_mask, _target["mask"], band_px=16)
                        _depth_evidence.update({
                            "depth_source": getattr(
                                ep, "depth_source", "unknown"),
                            "depth_confidence": getattr(
                                ep, "depth_confidence", 0.5),
                        })
                        _transition = _box_contact_transition(
                            _history, f, _gap_now, ep.timestamps)
                        _roi = [max(0.0, min(_box[0], _uv[0]) - 45),
                                max(0.0, min(_box[1], _uv[1]) - 45),
                                min(ep.rgb.shape[2], max(_box[2], _uv[0]) + 45),
                                min(ep.rgb.shape[1], max(_box[3], _uv[1]) + 45)]
                        self.engine._fire(CollisionEvent(
                            f, t_rel, "intersection", f"gripper:{_arm}", _tid,
                            {"motion_state": "sam3_unbound_gripper_target_contact",
                             "collision_valid": bool(_depth_evidence.get(
                                 "depth_contact_consistent") is True),
                             "needs_review": True,
                             "tcp_box_gap_px": round(_gap_now, 1),
                             "sam_score": round(_target["score"], 3),
                             "roi_xyxy": [round(v, 1) for v in _roi],
                             "roi_includes_both": True,
                             "view": "faceImg",
                             **_transition, **_depth_evidence},
                            f"未绑定夹爪[{_arm}]与任务物体[{_tent.name}]经 "
                            "TCP投影、SAM3实例mask及局部深度生成接触候选；"
                            "请 VLM 区分正常抓取与意外磕碰"))

                # 闭合夹爪已到达杯子附近，但没有任何可观测运动目标可以解释
                # 该操作：生成一次遮挡复核候选。它只提供 VLM ROI，不作为
                # 有效几何碰撞，避免 480s 一类全遮挡接触完全没有裁决窗口。
                _has_active_visible_target = any(
                    (eid in driven_targets or ent.bonded_to is not None
                     or tracker_motion_count.get(eid, 0) >= 1)
                    and track_status.get(eid) in {"tracking", "bonded", "driven"}
                    for eid, ent in tracked.items()
                    if ent.category == "A_target")
                if not _has_active_visible_target:
                    for _arm, _ee in ee_cams.items():
                        if ep.arm_state_at(f, _arm)["gripper"] > GRASP_CLOSE_TH:
                            continue
                        _uv = ep.project_to_pixels(_ee.reshape(1, 3))[0]
                        _near_cups = []
                        for _ob in graph.obstacles():
                            _box = track_boxes.get(_ob.eid)
                            if _box is None:
                                continue
                            _dx = max(0.0, _box[0] - _uv[0], _uv[0] - _box[2])
                            _dy = max(0.0, _box[1] - _uv[1], _uv[1] - _box[3])
                            _near_cups.append((float(np.hypot(_dx, _dy)), _ob))
                        if not _near_cups:
                            continue
                        _px_gap, _ob = min(_near_cups, key=lambda item: item[0])
                        if _px_gap > 55:
                            continue
                        _targets = [ent for ent in graph.targets()
                                    if ent.bonded_to is None and ent.eid in centers]
                        if not _targets:
                            continue
                        _tent = min(_targets, key=lambda ent: float(
                            np.linalg.norm(centers[ent.eid] - _ee)))
                        _pair = (_tent.eid, _ob.eid)
                        if t_rel - review_candidate_last.get(_pair, -1e9) < 10.0:
                            continue
                        review_candidate_last[_pair] = float(t_rel)
                        self.engine._fire(CollisionEvent(
                            f, t_rel, "intersection", _tent.eid, _ob.eid,
                            {"motion_state": "occluded_tcp_near_cup",
                             "collision_valid": False,
                             "needs_review": True,
                             "visibility": "unobservable",
                             "tcp_cup_gap_px": round(_px_gap, 1)},
                            f"闭合夹爪[{_arm}]已到达杯子[{_ob.name}]附近，但"
                            f"任务物体[{_tent.name}]当前无可靠运动观测；请 VLM "
                            "检查是否发生物体—杯子接触，先验置信度应降低"))

                # 主视角无法解释当前操作时切换到同步腕部视角。搜索对象对由
                # TaskGraph 的 contact policy 编译得到，腕部视角只提供二维接触
                # 与相对反应证据，不把透视重叠直接当成确定几何碰撞。
                if (not _has_active_visible_target and (f == 0 or int(t_rel + 1e-9) > int(ep.timestamps[max(0, f-self.stride)] - ep.timestamps[0] + 1e-9))
                        and prev_ee_cams):
                    _moving_arms = [
                        (float(np.linalg.norm(_ee - prev_ee_cams[_arm])), _arm)
                        for _arm, _ee in ee_cams.items()
                        if _arm in prev_ee_cams]
                    if _moving_arms:
                        _arm_motion, _arm = max(_moving_arms)
                    else:
                        _arm_motion, _arm = 0.0, ""
                    if _arm:
                        _view = ("rightImg" if "right" in _arm else "leftImg")
                        _aux = self._aux_frame(_view, f)
                        if _aux is not None:
                            _cross_specs = (self.task_graph.cross_view_pairs()
                                            if self.task_graph is not None else [])
                            _eligible = []
                            for _subject_sem, _object_sem, _policy in _cross_specs:
                                _subject_ent = next((ent for ent in graph.targets()
                                    if self.task_graph.resolve(ent.name) == _subject_sem
                                    and track_status.get(ent.eid) in {
                                        "occluded", "reidentifying"}), None)
                                if _subject_ent is not None:
                                    _eligible.append((_subject_ent, _subject_sem,
                                                      _object_sem, _policy))
                            _selected = _select_cross_view_subject(
                                _eligible, driven_targets, tracker_motion_count,
                                driven_count, driven_last_frame)
                            _subject_ent = _selected[0] if _selected else None
                            _subject_concept = (_selected[1].semantic_group
                                                if _selected else "")
                            _object_concept = (_selected[2].semantic_group
                                               if _selected else "")
                            _pots = self.sam3.segment_all(
                                _aux, _subject_concept, max_box_width_ratio=0.8
                            ) if _subject_concept else []
                            _cups = self.sam3.segment_all(
                                _aux, _object_concept, max_box_width_ratio=0.7
                            ) if _object_concept else []
                            if self.appearance is not None and _subject_ent is not None:
                                _scores, _ = self.appearance.score_candidates(
                                    _subject_ent.eid, _aux,
                                    [pot["mask"] for pot in _pots],
                                    competitors=[ent.eid for ent in graph.targets()
                                                 if ent.eid != _subject_ent.eid])
                                _score_by_index = {score.index: score
                                                   for score in _scores}
                                _pots = [pot for idx, pot in enumerate(_pots)
                                    if idx in _score_by_index
                                    and _aux_identity_valid(
                                        pot["mask"],
                                        [cup["mask"] for cup in _cups],
                                        _score_by_index[idx].similarity,
                                        _score_by_index[idx].margin)]
                            _aux_pairs = [(_box_gap(pot["box"], cup["box"]), pot, cup)
                                          for pot in _pots for cup in _cups]
                            _nearest_aux = min(_aux_pairs, key=lambda item: item[0],
                                               default=None)
                            _aux_transition = {}
                            if _nearest_aux is not None:
                                _aux_hist = aux_gap_history.setdefault(_view, [])
                                _aux_hist.append((f, _nearest_aux[0]))
                                del _aux_hist[:-8]
                                _aux_transition = _box_contact_transition(
                                    _aux_hist, f, _nearest_aux[0], ep.timestamps,
                                    lookback_frames=30)
                            _touch = next(((_pot, _cup)
                                for _pot in _pots for _cup in _cups
                                if min(_pot["score"], _cup["score"]) >= 0.45
                                and _masks_within(
                                    _pot["mask"], _cup["mask"], pixels=8)), None)
                            _prior_noncontact = aux_noncontact_streak.get(_view, 0)
                            _new_streak, _onset = _contact_onset_after_absence(
                                _prior_noncontact, _touch is not None)
                            aux_noncontact_streak[_view] = _new_streak
                            if _touch is not None:
                                _aux_transition["first_contact_transition"] = _onset
                                _aux_transition["prior_noncontact_scans"] = (
                                    _prior_noncontact)
                            _relative_reaction_px = None
                            if (_touch is not None and _onset
                                    and _arm_motion >= 0.0025):
                                _aux_before = self._aux_frame(_view, max(0, f - 5))
                                if _aux_before is not None:
                                    _prior_pots = self.sam3.segment_all(
                                        _aux_before, _subject_concept,
                                        max_box_width_ratio=0.8)
                                    _prior_cups = self.sam3.segment_all(
                                        _aux_before, _object_concept,
                                        max_box_width_ratio=0.7)
                                    _prior_pairs = [(pot["mask"], cup["mask"])
                                        for pot in _prior_pots for cup in _prior_cups
                                        if min(pot["score"], cup["score"]) >= 0.45
                                        and _masks_within(
                                            pot["mask"], cup["mask"], pixels=8)]
                                    _relative_reaction_px = _relative_pair_displacement(
                                        _touch[0]["mask"], _touch[1]["mask"],
                                        _prior_pairs)
                            # 静止阶段的透视贴边不是运行时碰撞。480s 真碰撞
                            # 实测臂步进约3.2mm；534/538s静态伪候选仅1.1/1.7mm。
                            if (_touch is not None and _arm_motion >= 0.0025
                                    and _onset and _relative_reaction_px is not None
                                    and _relative_reaction_px >= 8.0):
                                _boxes = [_touch[0]["box"], _touch[1]["box"]]
                                _rx0 = max(0.0, min(b[0] for b in _boxes) - 30)
                                _ry0 = max(0.0, min(b[1] for b in _boxes) - 30)
                                _rx1 = min(_aux.shape[1], max(b[2] for b in _boxes) + 30)
                                _ry1 = min(_aux.shape[0], max(b[3] for b in _boxes) + 30)
                                _primary_status = (track_status.get(
                                    _subject_ent.eid, "tracking")
                                    if _subject_ent is not None else "missing")
                                # 备用视角只验证主视角不可观测的任务实例；稳定
                                # 跟踪中的实例不能被腕部视角相似物体覆盖身份。
                                if (_subject_ent is not None and _primary_status in {
                                        "occluded", "reidentifying"}):
                                    _pair = (_subject_ent.eid,
                                             f"aux:{_view}:{_object_concept}")
                                    if (f - review_candidate_last.get(
                                            _pair, -10**9) >= 120):
                                        review_candidate_last[_pair] = float(t_rel)
                                        self.engine._fire(CollisionEvent(
                                            f, t_rel, "intersection",
                                            _subject_ent.eid, _pair[1],
                                            {"motion_state":
                                                "aux_sam3_mask_contact",
                                             "collision_valid": False,
                                             "needs_review": True,
                                             "visibility": "aux_visible",
                                             "primary_status": _primary_status,
                                             "view": _view,
                                             "arm_motion_m": round(
                                                _arm_motion, 4),
                                             "relative_reaction_px": round(
                                                 _relative_reaction_px, 2),
                                             "sam_scores": [round(
                                                _touch[0]["score"], 3), round(
                                                _touch[1]["score"], 3)],
                                             "roi_xyxy": [round(_rx0, 1),
                                                          round(_ry0, 1),
                                                          round(_rx1, 1),
                                                          round(_ry1, 1)],
                                             **_aux_transition},
                                            f"主视角无可靠目标运动；同步备用视角"
                                            f"[{_view}]中 SAM3 确认"
                                            f"{_subject_concept}与{_object_concept} mask "
                                            "接触，请 VLM 多视角复核真实接触反应"))

            prev_target_centers = {
                eid: centers[eid].copy() for eid, ent in tracked.items()
                if ent.category == "A_target" and eid in centers}
            prev_entity_centers = {eid: center.copy()
                                   for eid, center in centers.items()}
            prev_ee_cams = {arm: center.copy() for arm, center in ee_cams.items()}

            # —— 碰撞评估 ——
            collision_ellipsoids = {
                eid: ell for eid, ell in ellipsoids.items()
                if (eid not in track_status
                    or track_status[eid] in {"tracking", "bonded", "driven"})}
            target_states = {
                eid: {
                    "status": track_status.get(eid, "tracking").upper(),
                    "visible": track_status.get(eid, "tracking") in {"tracking", "bonded", "driven"},
                    "collision_valid": track_status.get(eid, "tracking") in {"tracking", "bonded", "driven"},
                    "confidence_scale": _observability_scale(track_status.get(eid, "tracking")),
                }
                for eid, ent in graph.entities.items()
                if ent.category == "A_target"
            }
            self.observability_history[f] = {
                "frame": f,
                "task_target_visible": any(x["visible"] for x in target_states.values()),
                "targets": target_states,
            }
            # Compact, model-independent trajectory evidence for downstream lid
            # state-chain detection.  Annotation data is intentionally absent.
            for _eid, _state in target_states.items():
                if _eid not in centers:
                    continue
                _entity = graph.entities[_eid]
                _table = next((x.plane for x in graph.planes()
                               if x.plane is not None), None)
                _table_distance = (None if _table is None else abs(float(
                    centers[_eid] @ _table.n + _table.d)))
                self.target_state_samples.append({
                    "frame": f, "time_sec": round(float(t_rel), 3),
                    "eid": _eid, "name": _entity.name,
                    "center": [round(float(x), 5) for x in centers[_eid]],
                    "status": _state["status"].lower(),
                    "controlled": bool(_entity.bonded_to)
                        or _state["status"].lower() in {"bonded", "driven"},
                    "collision_valid": bool(_state["collision_valid"]),
                    "table_distance_m": (None if _table_distance is None else
                                         round(_table_distance, 5)),
                })
            new_ev = self.engine.evaluate_frame(
                f, t_rel, graph, collision_ellipsoids,
                driven_targets=driven_targets,
                forbidden_target_pairs=forbidden_target_pairs)
            # 为 VLM 保存接触对象在主视角中的联合 ROI。后续裁决会显示
            # “全景红框 + ROI 放大”，避免小接触在整幅 720p 中不可见。
            for ev in new_ev:
                boxes = [track_boxes[eid] for eid in (ev.subject, ev.object_)
                         if eid in track_boxes]
                _gripper_arm = next((eid.split(":", 1)[1]
                    for eid in (ev.subject, ev.object_)
                    if eid.startswith("gripper:") and eid.split(":", 1)[1] in ee_cams), None)
                if _gripper_arm is not None:
                    _tcp_uv = ep.project_to_pixels(
                        ee_cams[_gripper_arm].reshape(1, 3))[0]
                    boxes.append([_tcp_uv[0]-25, _tcp_uv[1]-25,
                                  _tcp_uv[0]+25, _tcp_uv[1]+25])
                    ev.detail["roi_includes_both"] = len(boxes) >= 2
                if boxes and "roi_xyxy" not in ev.detail:
                    x0 = min(box[0] for box in boxes)
                    y0 = min(box[1] for box in boxes)
                    x1 = max(box[2] for box in boxes)
                    y1 = max(box[3] for box in boxes)
                    pad = max(35.0, .2 * max(x1 - x0, y1 - y0))
                    ev.detail["roi_xyxy"] = [round(max(0, x0-pad), 1),
                                              round(max(0, y0-pad), 1),
                                              round(min(ep.rgb.shape[2], x1+pad), 1),
                                              round(min(ep.rgb.shape[1], y1+pad), 1)]
                    ev.detail.setdefault("view", "faceImg")
                if ev.subject in centers and ev.subject in prev_entity_centers:
                    ev.detail.setdefault("subject_step_m", round(float(
                        np.linalg.norm(centers[ev.subject]
                                       - prev_entity_centers[ev.subject])), 4))
                _pair = (ev.subject, ev.object_)
                if _pair in pair_gap_history:
                    ev.detail.update({key: value for key, value in
                        _box_contact_transition(pair_gap_history[_pair], f,
                            pair_gap_history[_pair][-1][1], ep.timestamps).items()
                        if key not in ev.detail})
            for ev in new_ev:
                self._log(f"  [f{f} {t_rel:6.2f}s] {ev.trigger}: {ev.message}")
            # 可视化钩子
            if self.frame_callback is not None:
                self.frame_callback(f, t_rel, graph, ellipsoids, new_ev,
                                    {"tracks": tracks, "visibs": visibs,
                                     "owner": owner, "grippers": self.grippers,
                                     "ee_prev": ee_prev,
                                     "track_status": dict(track_status)})
        return self

    # ─────────────── Phase 3: VLM 裁决 ───────────────
    def phase3_adjudicate(self, max_events: int | None = None):
        if not self.use_vlm_adjudicator:
            return self
        from .adjudicator import VLMAdjudicator
        self.adjudicator = VLMAdjudicator()
        self.collision_segments = merge_collision_events(
            self.engine.events, task_graph=self.task_graph)
        segments = self.collision_segments
        if max_events is not None:
            segments = segments[:max_events]
        for ev in segments:
            views = ev.detail.get("views", [])
            has_roi = bool(ev.detail.get("roi_xyxy"))
            seg_start = float(ev.detail.get("segment_start_sec", ev.time_sec))
            seg_end = float(ev.detail.get("segment_end_sec", ev.time_sec))
            hypotheses = list(ev.detail.get("evidence_hypothesis_times_sec",
                                             [ev.time_sec]))
            if "aux_sam3" in str(ev.detail.get("motion_state", "")):
                backtrack = max(3.0, float(ev.detail.get(
                    "prior_noncontact_scans", 0)))
                hypotheses.append(max(0.0, ev.time_sec - backtrack))
            hypotheses = sorted(set(round(float(x), 3) for x in hypotheses))
            ev.detail["evidence_hypothesis_times_sec"] = hypotheses
            evidence_start = min([seg_start, *hypotheses])
            evidence_end = max([seg_end, *hypotheses])
            clip_center_sec = (evidence_start + evidence_end) / 2
            clip_radius_sec = max(1.0 if has_roi else 2.0,
                                  (evidence_end - evidence_start) / 2 + 0.6)
            clip_center_frame = int(np.argmin(np.abs(
                self.ep.timestamps - self.ep.timestamps[0] - clip_center_sec)))
            ev.detail["clip_start_sec"] = max(0.0,
                                               clip_center_sec - clip_radius_sec)
            ev.detail["clip_end_sec"] = clip_center_sec + clip_radius_sec
            clip = self._clip_around(clip_center_frame, clip_radius_sec,
                                     views=views, detail=ev.detail)
            prior = self._observability_at(ev.frame_idx, ev.subject, ev.object_)
            # 备用同步视角若直接看到了接触，就不能被主视角的遮挡状态
            # 一票否决；VLM 将同时看到主视角与该备用视角。
            aux_views = [v for v in views if v in {"leftImg", "rightImg"}]
            if aux_views and (ev.detail.get("visibility") == "aux_visible"
                              or ev.detail.get("collision_valid", True)):
                prior.update({
                    "task_target_visible": True,
                    "collision_valid": True,
                    "confidence_scale": 1.0,
                    "message": ("主视角可能遮挡，但同步备用视角 "
                                + ", ".join(aux_views)
                                + " 可观察候选接触；请以拼接视频裁决。"),
                })
            elif has_roi and prior.get("collision_valid", True):
                prior["confidence_scale"] = 1.0
            verdict = self.adjudicator.judge(
                ev, clip, self.ep.task_desc, observability=prior,
                task_policy=(self.task_graph.to_dict()
                             if self.task_graph is not None else None))
            self.events_adjudicated.append({**ev.to_dict(), "verdict": verdict})
        return self

    def _observability_at(self, frame_idx: int, subject: str = "",
                          object_: str = "") -> dict:
        if not self.observability_history:
            return {"task_target_visible": False, "confidence_scale": 0.2,
                    "message": "当前视频片段无法可靠观察任务物体"}
        key = min(self.observability_history, key=lambda x: abs(x - frame_idx))
        prior = dict(self.observability_history[key])
        involved = [x for x in (subject, object_) if x in prior["targets"]]
        scales = [prior["targets"][x]["confidence_scale"] for x in involved]
        if not scales:
            scales = [x["confidence_scale"] for x in prior["targets"].values()]
        prior["confidence_scale"] = min(scales, default=0.2)
        prior["collision_valid"] = all(
            prior["targets"][x]["collision_valid"] for x in involved)
        prior["message"] = ("任务物体可见且身份已确认" if prior["task_target_visible"]
                            else "当前视频片段无法可靠观察任务物体")
        return prior

    def _reground(self, ent, frame_idx: int,
                  ref_center: np.ndarray | None = None,
                  claimed_masks: list | None = None,
                  max_dist: float | None = None,
                  other_targets: list | None = None,
                  return_mask: bool = False,
                  allow_global: bool = False,
                  association_center: np.ndarray | None = None,
                  track_uv: np.ndarray | None = None,
                  track_visibility: np.ndarray | None = None):
        """SAM3 在 frame_idx 重新分割目标 → 反投影点云中值 = 新中心。
        抓取瞬间 CoTracker 已丢目标 (遮挡) 时用, 冻结中心不可信。
        返回 (3,) 中心或 None。return_mask=True 时返回 (center, mask)。

        多实例: 名称带数字后缀 (cup1/cup2) → 用基础概念 segment_all,
        A_target 也用 segment_all (可能有同名部件如两个壶的盖子),
        选离 ref_center 最近且通过互斥检查的实例。

        防串实例保护:
          - 距离上限: 最近实例距 ref_center 超过 max(0.15, 0.3·z) → 拒绝 (宁lost不串)
          - 互斥占用: 已被其他实体认领 (mask IoU>0.3) 的实例跳过
          - A_target 互斥: 候选中心离其他 A_target 太近 (在其椭球上) → 拒绝
        """
        if self.sam3 is None or ent.mask is None:
            return None
        try:
            import re
            base = re.sub(r'\d+$', '', ent.name).strip()
            # 多实例: 数字后缀实体 (cup1→cup) 或 A_target (可能有同名部件)
            if (base and base != ent.name) or ent.category == "A_target":
                # A_target 用名称最后一词作 prompt (teapot lid → lid 更宽泛, 能找到真壶盖)
                if base == ent.name and ent.category == "A_target":
                    _prompt = ent.name.split()[-1]
                else:
                    _prompt = base
                all_res = self.sam3.segment_all(
                    self.ep.rgb[frame_idx], _prompt)
                if not all_res:
                    return None
                if ref_center is None and ent.ellipsoid is not None:
                    ref_center = ent.ellipsoid.center
                best, best_d, best_mask = None, 1e9, None
                viable: list[tuple[np.ndarray, np.ndarray]] = []
                viable_track_support: list[float] = []
                for r in all_res:
                    if r["mask"] is None or r["mask"].sum() < 200:
                        continue
                    r_mask = r["mask"]
                    if claimed_masks:
                        skip = False
                        for cm in claimed_masks:
                            inter = int(np.logical_and(r_mask, cm).sum())
                            if inter > 0:
                                union = int(np.logical_or(r_mask, cm).sum())
                                if union > 0 and inter / union > 0.15:
                                    skip = True
                                    break
                        if skip:
                            continue
                    pc = self.ep.backproject(
                        r_mask.astype(bool), frame_idx, stride=4)
                    if len(pc) < 20:
                        continue
                    c = np.median(pc, axis=0)
                    if (allow_global and ent.category == "A_target"
                            and ent.ellipsoid is not None
                            and not _global_relocation_height_valid(
                                float(c[2]), float(ent.ellipsoid.center[2]))):
                        continue
                    # A_target z 上限: 候选明显高于 init → 假阳性 (在其他物体上)
                    # cap 升级时按比例放宽 (物体被搬起 z 自然升高)
                    if (ent.category == "A_target" and ent.ellipsoid is not None
                            and not allow_global):
                        _z_init = float(ent.ellipsoid.center[2])
                        _z_allow = 0.15 + max(0, (max_dist or 0.35) - 0.35) * 0.5
                        if c[2] > _z_init + _z_allow:
                            continue
                    # A_target 互斥: 候选在其他 A_target 椭球内 且 明显高于自身 init → 跳过
                    if other_targets:
                        _z_init = float(ent.ellipsoid.center[2])
                        _conflict = False
                        for _other in other_targets:
                            _oe, _tc, _tr = (_other if len(_other) == 3
                                             else (None, _other[0], _other[1]))
                            if _identity_candidate_conflict(
                                    ent.eid, _oe, c, _tc, _tr):
                                _conflict = True
                                break
                            if (np.linalg.norm(c - _tc) < _tr
                                    and c[2] > _z_init + 0.10):
                                _conflict = True
                                break
                        if _conflict:
                            continue
                    viable.append((c, r_mask))
                    viable_track_support.append(_mask_track_overlap(
                        r_mask, track_uv, track_visibility))
                    _rank_ref = association_center if association_center is not None else ref_center
                    if _rank_ref is not None:
                        d = float(np.linalg.norm(c - _rank_ref))
                        if d < best_d:
                            best_d, best, best_mask = d, c, r_mask
                    elif best is None:
                        best, best_mask = c, r_mask
                _reid_embedding = None
                _reid_relocation_confirmed = False
                if (ent.category == "A_target" and self.appearance is not None
                        and viable):
                    _scores, _embs = self.appearance.score_candidates(
                        ent.eid, self.ep.rgb[frame_idx],
                        [m for _, m in viable],
                        competitors=list(self.appearance.templates))
                    _score = next((s for s in _scores
                                   if self.appearance.passes(s)), None)
                    if _score is None:
                        if _scores:
                            s = _scores[0]
                            self._log(f"  ! [f{frame_idx}] ReID {ent.eid}: "
                                      f"sim={s.similarity:.3f}, "
                                      f"margin={s.margin:.3f} → 拒绝")
                        self.appearance.reject(ent.eid)
                        return None
                    best, best_mask = viable[_score.index]
                    _reid_embedding = _embs[_score.index]
                    _rank_ref = (association_center if association_center is not None
                                 else ref_center)
                    best_d = (float(np.linalg.norm(best - _rank_ref))
                              if _rank_ref is not None else 0.0)
                    _cap = None
                    if ref_center is not None:
                        _cap = (max_dist if max_dist is not None
                                else max(0.15, 0.3 * float(ref_center[2])))
                    _distant = _cap is not None and best_d > _cap
                    _needs_confirmation = allow_global or _distant
                    # 原实例 CoTracker 点仍集中落在该 SAM mask 内时，二维身份
                    # 连续性可替代抖动较大的三维位置三连确认；外观与竞争实体
                    # margin 仍必须先通过，故不会仅凭漂移点解锁。
                    _track_supported = viable_track_support[_score.index] >= 0.35
                    if _track_supported:
                        _needs_confirmation = False
                        self._log(f"  + [f{frame_idx}] ReID {ent.eid}: "
                                  f"CoTracker-mask 支持="
                                  f"{viable_track_support[_score.index]:.2f}")
                    if (_needs_confirmation and not self.appearance.confirm(
                            ent.eid, best, _reid_embedding)):
                        _pending = self.appearance.pending[ent.eid]
                        self.track_transitions.append({
                            "frame": frame_idx, "eid": ent.eid,
                            "status": "reidentifying",
                            "similarity": round(_score.similarity, 4),
                            "margin": round(_score.margin, 4),
                            "confirmations": _pending.count})
                        self._log(f"  ~ [f{frame_idx}] ReID {ent.eid}: 候选确认 "
                                  f"{_pending.count}/{self.appearance.cfg.confirm_frames}")
                        return None
                    _reid_relocation_confirmed = _needs_confirmation
                    self._log(f"  + [f{frame_idx}] ReID {ent.eid}: "
                              f"sim={_score.similarity:.3f}, "
                              f"margin={_score.margin:.3f}")
                if best is not None:
                    if ref_center is not None and not allow_global:
                        _z_ref = float(ref_center[2])
                        _cap = max_dist if max_dist is not None else max(0.15, 0.3 * _z_ref)
                        if best_d > _cap and not _reid_relocation_confirmed:
                            self._log(f"  ! 重锚定 {ent.eid}: 最近实例 "
                                      f"{best_d*100:.0f}cm > cap "
                                      f"{_cap*100:.0f}cm → 拒绝 (防串实例)")
                            return None
                    if claimed_masks is not None:
                        claimed_masks.append(best_mask)
                    if _reid_embedding is not None and self.appearance is not None:
                        self.appearance.accept(ent.eid, _reid_embedding)
                        self.track_transitions.append({
                            "frame": frame_idx, "eid": ent.eid,
                            "status": "tracking", "reason": "reid_confirmed"})
                    return (best, best_mask) if return_mask else best
                return None
            res = self.sam3.segment(self.ep.rgb[frame_idx], ent.name)
            if res["mask"] is None or res["mask"].sum() < 200:
                return None
            pc = self.ep.backproject(res["mask"].astype(bool),
                                     frame_idx, stride=4)
            if len(pc) < 20:
                return None
            c = np.median(pc, axis=0)
            if (allow_global and ent.category == "A_target"
                    and ent.ellipsoid is not None
                    and not _global_relocation_height_valid(
                        float(c[2]), float(ent.ellipsoid.center[2]))):
                self._log(f"  ! 重锚定 {ent.eid}: 全局候选 z={c[2]:.2f} "
                          f"不在桌面支撑带 → 拒绝")
                return None
            # A_target z 上限: 候选明显高于 init → 假阳性
            # cap 升级时按比例放宽 (物体被搬起 z 自然升高)
            if (ent.category == "A_target" and ent.ellipsoid is not None
                    and not allow_global):
                _z_init = float(ent.ellipsoid.center[2])
                _z_allow = 0.15 + max(0, (max_dist or 0.35) - 0.35) * 0.5
                if c[2] > _z_init + _z_allow:
                    self._log(f"  ! 重锚定 {ent.eid}: z={c[2]:.2f} > "
                              f"{_z_init+_z_allow:.2f} → 拒绝 (z过高)")
                    return None
            # A_target 互斥: 候选在其他 A_target 椭球内 且 明显高于自身 init → 拒绝
            if other_targets:
                _z_init = float(ent.ellipsoid.center[2])
                for _other in other_targets:
                    _oe, _tc, _tr = (_other if len(_other) == 3
                                     else (None, _other[0], _other[1]))
                    if _identity_candidate_conflict(ent.eid, _oe, c, _tc, _tr):
                        self._log(f"  ! 重锚定 {ent.eid}: 候选与 {_oe} "
                                  f"实例冲突 → 拒绝")
                        return None
                    if np.linalg.norm(c - _tc) < _tr and c[2] > _z_init + 0.10:
                        self._log(f"  ! 重锚定 {ent.eid}: 候选在其他 A_target 上 "
                                  f"(d={np.linalg.norm(c - _tc)*100:.0f}cm, "
                                  f"z={c[2]:.2f}>{_z_init+0.10:.2f}) → 拒绝")
                        return None
            if ref_center is not None and not allow_global:
                _d = float(np.linalg.norm(c - ref_center))
                _z_ref = float(ref_center[2])
                _cap = max_dist if max_dist is not None else max(0.15, 0.3 * _z_ref)
                if _d > _cap:
                    self._log(f"  ! 重锚定 {ent.eid}: 文本实例距参考 "
                              f"{_d*100:.0f}cm > cap {_cap*100:.0f}cm → 拒绝")
                    return None
            if claimed_masks is not None:
                claimed_masks.append(res["mask"])
            return (c, res["mask"]) if return_mask else c
        except Exception as exc:
            self._log(f"  ! 重锚定 {ent.eid} 失败: {exc}")
            return None

    def _tcp_in_mask(self, ee_cam: np.ndarray, mask: np.ndarray,
                     near_px: int = 0) -> bool:
        """TCP 投影到图像, 检查是否落在 mask 内 (或 near_px 像素内)。
        near_px>0 时用扩张 bbox 兼容夹爪遮挡 (TCP 在 mask 边缘但不被覆盖)。"""
        uv = self.ep.project_to_pixels(ee_cam.reshape(1, 3))
        u, v = int(uv[0, 0]), int(uv[0, 1])
        H, W = mask.shape
        if not (0 <= u < W and 0 <= v < H):
            return False
        if mask[v, u]:
            return True
        if near_px > 0:
            ys, xs = np.where(mask)
            if len(ys) == 0:
                return False
            return (xs.min() - near_px <= u <= xs.max() + near_px and
                    ys.min() - near_px <= v <= ys.max() + near_px)
        return False

    def _clip_around(self, frame_idx: int, sec: float = 2.0,
                     views: list[str] | None = None,
                     detail: dict | None = None) -> Path:
        """截取事件片段；有备用视角证据时输出主/辅同步拼接视频。"""
        t = float(self.ep.timestamps[frame_idx] - self.ep.timestamps[0])
        detail = detail or {}
        aux = next((v for v in (views or []) if v in {"leftImg", "rightImg"}
                    and (Path(self.ep.episode_dir) / f"{v}.mp4").exists()), None)
        roi = detail.get("roi_xyxy")
        suffix = f"_{aux}" if aux else ""
        if roi:
            suffix += "_zoom"
        out = self.out / f"clip_f{frame_idx}{suffix}.mp4"
        start, duration = f"{max(0, t - sec):.2f}", f"{2 * sec:.2f}"
        if roi:
            source = (Path(self.ep.episode_dir) / f"{aux}.mp4"
                      if aux else Path(self.ep.video_path))
            x0, y0, x1, y1 = [int(round(float(v))) for v in roi]
            width, height = max(16, x1 - x0), max(16, y1 - y0)
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-ss", start, "-t", duration,
                "-i", str(source), "-filter_complex",
                (f"[0:v]split[a][b];[a]drawbox=x={x0}:y={y0}:w={width}:h={height}:"
                 f"color=red@0.8:t=5,scale=640:360[full];"
                 f"[b]crop={width}:{height}:{x0}:{y0},scale=640:360[zoom];"
                 "[full][zoom]hstack=2[out]"),
                "-map", "[out]", "-c:v", "libx264", "-crf", "20", str(out)
            ], check=False)
        elif aux:
            subprocess.run([
                "ffmpeg", "-y", "-v", "error",
                "-ss", start, "-t", duration, "-i", str(self.ep.video_path),
                "-ss", start, "-t", duration, "-i",
                str(Path(self.ep.episode_dir) / f"{aux}.mp4"),
                "-filter_complex",
                "[0:v]scale=640:360[a];[1:v]scale=640:360[b];[a][b]hstack=2[out]",
                "-map", "[out]", "-c:v", "libx264", "-crf", "23", str(out)
            ], check=False)
        else:
            subprocess.run(["ffmpeg", "-y", "-v", "error",
                            "-ss", start, "-t", duration,
                            "-i", str(self.ep.video_path),
                            "-c:v", "libx264", "-crf", "23", str(out)], check=False)
        return out

    def _log(self, msg: str):
        if self.debug:
            print(msg, flush=True)

    def report(self) -> dict:
        return {
            "episode": Path(self.ep.episode_dir).name,
            "n_frames": self.ep.n_frames,
            "events": [e.to_dict() for e in self.engine.events],
            "collision_segments": [e.to_dict() for e in self.collision_segments],
            "adjudicated": self.events_adjudicated,
            "track_transitions": self.track_transitions,
            "target_state_samples": self.target_state_samples,
            "observability": list(self.observability_history.values()),
            "task_graph": self.task_graph.to_dict() if self.task_graph else None,
        }


# ── 小工具 ──
def _observability_scale(status: str) -> float:
    return {"tracking": 1.0, "bonded": 1.0, "driven": 0.85,
            "reidentifying": 0.5, "occluded": 0.2}.get(status.lower(), 0.2)


def _mask_track_overlap(mask: np.ndarray, uv: np.ndarray | None,
                        visibility: np.ndarray | None) -> float:
    """可见 CoTracker 点落入候选 mask 的比例，用作实例连续性证据。"""
    if uv is None or visibility is None:
        return 0.0
    points = np.rint(np.asarray(uv)).astype(int)
    visible = np.asarray(visibility) > 0.5
    h, w = mask.shape[:2]
    valid = (visible & (points[:, 0] >= 0) & (points[:, 0] < w)
             & (points[:, 1] >= 0) & (points[:, 1] < h))
    if np.count_nonzero(valid) < 8:
        return 0.0
    p = points[valid]
    return float(np.mean(mask[p[:, 1], p[:, 0]] > 0))


def _global_relocation_height_valid(candidate_z: float, initial_z: float,
                                    tolerance: float = 0.15) -> bool:
    """未被机器人夹持的全局候选必须已经回到支撑面附近。

    人手搬运过程没有可靠的人手位姿，空中检测只保留 LOST；等物体重新放到
    桌面后再恢复碰撞实体，避免把 kettle 顶部等高处误认为 teapot。
    """
    return abs(float(candidate_z) - float(initial_z)) <= float(tolerance)


def _identity_candidate_conflict(target_eid: str, other_eid: str | None,
                                 candidate: np.ndarray,
                                 other_center: np.ndarray,
                                 other_radius: float) -> bool:
    """容器及其部件的永久实例约束。

    `teapot_lid` 可以与 teapot 重叠（开始/盖回时本来就在茶壶上），
    但不能落入 kettle 的实体区域；`kettle_lid` 反之亦然。
    `teapot` 与 `kettle` 本体也不能互相占用对方的实体区域。
    该约束不依赖高度，避免两个盖子 z 接近时发生身份交换。
    """
    if not other_eid:
        return False
    target = target_eid.lower().replace("-", "_")
    other = other_eid.lower().replace("-", "_")
    target_name = target.split(":")[-1]
    other_name = other.split(":")[-1]
    vessel_names = {"teapot", "kettle"}

    if target_name.endswith("_lid"):
        owner = target_name.rsplit("_lid", 1)[0]
        if owner and owner in other_name:
            return False
        if not any(name in other_name for name in vessel_names):
            return False
    elif target_name in vessel_names:
        # 全局重定位时 SAM3 容易混淆外观相近的壶。即使高度正常，
        # 候选中心落进另一种壶的占用区也不能接受。
        if other_name not in vessel_names or other_name == target_name:
            return False
    else:
        return False
    # 盖子可近似为小部件；壶本体必须使用两件物体的联合占用范围。
    # 当前 helper 未传入目标半径，0.12m 是两类壶较小的横向半径先验，
    # 比原先把候选中心视为点的 0.04m margin 更符合实体互斥。
    margin = 0.04 if target_name.endswith("_lid") else 0.12
    return float(np.linalg.norm(np.asarray(candidate) - np.asarray(other_center))) \
        < float(other_radius) + margin


def _task_target_phrases(task_desc: str) -> list[str]:
    """从任务描述抽取操作目标候选短语 (给 SAM3 做文本检测)。

    SAM3 对单词 "lid" 无响应, 但短语 "tissue box lid"/"lid of the
    tissue box" 可命中 —— 故优先抽完整名词短语, 再兜底单词。"""
    import re
    t = task_desc.lower()
    cands: list[str] = []

    def push(s):
        s = s.strip(" .,;")
        if s and len(s) >= 3 and s not in cands:
            cands.append(s)

    # 1) "the X of the Y" → 长短语 + 倒装变体 (如 "lid of the tissue box")
    for m in re.finditer(r"\bthe ([a-z][a-z ]{2,28}?) of the "
                         r"([a-z][a-z ]{2,20}?)(?= (?:on|in|into|from|back)\b|[;,.]|$)", t):
        push(f"{m.group(1)} of the {m.group(2)}")
        push(f"{m.group(2)} {m.group(1)}")  # "tissue box lid" 变体
    # 2) 动词后宾语: grab/place/pull/pick the X on/in/into/from ...
    for m in re.finditer(r"\b(?:grab|place|pull|pick|take) (?:the |a )?"
                         r"([a-z][a-z ]{2,24}?) (?:on|in|into|from|back|onto) ", t):
        push(m.group(1))
    # 3) 动词后宾语 (倒茶): pour ... into/from the X
    for m in re.finditer(r"\b(?:pour|open|close|fill) (?:the |a )?"
                         r"([a-z][a-z ]{2,24}?) (?:into|from|in|on|back) ", t):
        push(m.group(1))
    # 4) 兜底静态词表
    for word in ["tissue", "lid", "box lid", "cover", "cap", "paper towel",
                 "cup", "bottle", "napkin", "cloth",
                 "teapot", "kettle", "tea pot", "tea", "water",
                 "first cup", "second cup", "third cup"]:
        if word in t:
            push(word)
    return cands


def _quat_rotmat(q_xyzw: np.ndarray) -> np.ndarray:
    """xyzw 单位四元数 (ee_positions[3:7]) → 旋转矩阵。"""
    q = np.asarray(q_xyzw, np.float64)
    x, y, z, w = q
    n = np.linalg.norm([w, x, y, z])
    if n < 1e-6:
        return np.eye(3)
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def _rotmat_quat(R: np.ndarray) -> np.ndarray:
    """旋转矩阵 → wxyz 四元数。"""
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w, x, y, z = 0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w, x, y, z = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w, x, y, z = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w, x, y, z = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    return np.array([w, x, y, z])
