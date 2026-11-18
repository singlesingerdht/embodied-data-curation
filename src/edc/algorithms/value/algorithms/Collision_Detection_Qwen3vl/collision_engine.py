"""Module 3: 碰撞引擎 —— ACM 白名单 + 三大物理触发器。

白名单 (Allowed Collision Matrix) 逻辑 —— 抓取≠碰撞:
  ACM = { (gripper, target): ALLOWED,           # 抓取必须相交
          (bonded 实体内部):     ALLOWED }       # 握持期间 gripper↔target 视为一体
  真正告警的只有:
    ① 穿透: {gripper|target|bonded} 椭球穿透 B 类平面 (桌面/墙)
    ② 相交: {gripper|bonded} 椭球 × C 类障碍物椭球 (跳过 target —— 白名单)
    ③ 位移: C 类障碍物中心漂移 > ε (2cm) —— 被碰倒/推走的间接证据
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .geometry import (Ellipsoid, Plane, trigger_displacement,
                       trigger_intersection, trigger_penetration)


@dataclass
class CollisionEvent:
    """一次告警事件。"""
    frame_idx: int
    time_sec: float
    trigger: str                 # penetration / intersection / displacement
    subject: str                 # 主动实体 (gripper/target eid)
    object_: str                 # 被动实体 (plane/obstacle eid)
    detail: dict = field(default_factory=dict)
    message: str = ""            # 人类可读 (喂 VLM 裁决的物理先验)

    def to_dict(self):
        return {"frame": self.frame_idx, "time_sec": round(self.time_sec, 2),
                "trigger": self.trigger, "subject": self.subject,
                "object": self.object_, "detail": self.detail,
                "message": self.message}


class CollisionEngine:
    """逐帧评估三大触发器, 白名单前置过滤。"""

    def __init__(self,         penetration_margin: float = 0.03,  # 3cm: 超过基线穿透+此值才告警
                 intersection_shrink: float = 0.15,
                 displacement_thresh: float = 0.02,
                 cooldown_frames: int = 15,
                 report_gripper_plane_penetration: bool = False):
        """
        Args:
            penetration_margin: 穿透裕量 (米) —— 超过 基线穿透 + 此值才告警。
                                 基线 = 帧0 的初始穿透 (物体放在桌面上时椭球自然穿过平面)。
                                 只有穿透量 相对基线增加 超过 margin 才算异常
                                 (物体被按进桌面/砸到桌面)。
            intersection_shrink: 相交判定椭球收缩比
            displacement_thresh: 位移阈值 (米)
            cooldown_frames: 同 (subject,object,trigger) 冷却帧数
        """
        self.margin = penetration_margin
        self.shrink = intersection_shrink
        self.disp_th = displacement_thresh
        self.cooldown = cooldown_frames
        self.report_gripper_plane_penetration = report_gripper_plane_penetration
        self._last_fire: dict[tuple, int] = {}
        self._baseline_penetration: dict[tuple, float] = {}  # (eid, plane_eid) → 帧0穿透量
        self._last_penetration: dict[tuple, float] = {}
        self._penetration_growth_streak: dict[tuple, int] = {}
        self._pair_contact: dict[tuple[str, str], bool] = {}
        self._pending_direct_contact: dict[tuple[str, str], int] = {}
        self._unbound_pair_contact: dict[tuple[str, str], bool] = {}
        self._previous_centers: dict[str, np.ndarray] = {}
        self._displaced_obstacles: set[str] = set()
        self.events: list[CollisionEvent] = []

    def reset_support_baseline(self, eid: str, ellipsoid: Ellipsoid,
                               graph) -> None:
        """可信重定位/释放落桌后，把当前接触深度登记为正常支撑状态。"""
        for pe in graph.planes():
            key = (eid, pe.eid)
            depth = trigger_penetration(
                ellipsoid, pe.plane, margin=0)["penetration_depth"]
            self._baseline_penetration[key] = depth
            self._last_penetration[key] = depth
            self._penetration_growth_streak[key] = 0

    # ── 白名单 (ACM) ──
    @staticmethod
    def check_whitelist(e1: str, e2: str, graph) -> bool:
        """相交告警前的白名单检查。返回 True = 允许 (不算碰撞)。

        规则:
          ① gripper × target          → 允许 (抓取接触)
          ② 任一方 bonded_to 指向对方  → 允许 (握持期间视为一体)
          ③ 两实体同为 bonded 对的成员 → 允许
        """
        a, b = graph.entities.get(e1), graph.entities.get(e2)
        if a is None or b is None:
            return False
        if {a.category, b.category} == {"A_gripper", "A_target"}:
            if a.bonded_to == b.eid or b.bonded_to == a.eid:
                return True
            return True  # gripper-target 本就允许 (抓取阶段)
        if a.bonded_to == b.eid or b.bonded_to == a.eid:
            return True
        return False

    def _fire(self, ev: CollisionEvent):
        key = (ev.trigger, ev.subject, ev.object_)
        last = self._last_fire.get(key, -10**9)
        if ev.frame_idx - last < self.cooldown:
            return
        self._last_fire[key] = ev.frame_idx
        self.events.append(ev)

    # ── 主评估 ──
    def evaluate_frame(self, frame_idx: int, time_sec: float, graph,
                       ellipsoids: dict[str, Ellipsoid],
                       driven_targets: set[str] | None = None,
                       forbidden_target_pairs: set[frozenset[str]] | None = None,
                       ) -> list[CollisionEvent]:
        """ellipsoids: eid → 当前帧椭球 (A 类含 gripper 呼吸椭球/bonded 合并椭球;
        C 类为跟踪更新的椭球; B 类平面在 graph 实体上)。
        返回本帧新事件。"""
        n_before = len(self.events)
        driven_targets = driven_targets or set()
        forbidden_target_pairs = forbidden_target_pairs or set()

        # ① 穿透: 所有活动椭球 × 平面 (带初始基线: 物体放在桌上时椭球自然穿过平面不算)
        for eid, ell in ellipsoids.items():
            ent = graph.entities.get(eid)
            if ent is None or ent.category not in ("A_gripper", "A_target"):
                continue
            # TCP/arm ellipsoids are deliberately coarse and routinely cross
            # the estimated tabletop plane during normal operation.  Safety
            # events must be attributed to the carried object, not this proxy.
            if (ent.category == "A_gripper"
                    and not self.report_gripper_plane_penetration):
                continue
            if ent.category == "A_target" and ent.bonded_to is None:
                # 未绑定物体正常静置在支撑面上时，单目深度漂移会让椭球
                # 上下浮动。没有机器人夹爪接近，就不存在机器人造成的
                # 压入因果链，不能生成安全事件（本阶段也不建模人手）。
                robot_near = any(
                    gid.startswith("gripper:")
                    and np.linalg.norm(gell.center - ell.center)
                    <= (float(np.max(gell.radii))
                        + float(np.max(ell.radii)) + 0.15)
                    for gid, gell in ellipsoids.items())
                if not robot_near:
                    continue
            for pe in graph.planes():
                r = trigger_penetration(ell, pe.plane, margin=0)  # 先算原始穿透量
                depth = r["penetration_depth"]
                # 基线穿透: 帧0 的初始穿透 (物体放在桌面上时椭球自然穿过)
                bk = (eid, pe.eid)
                # 在线模式下实体可能在首帧之后才重锁出现，
                # 因此每个 (实体,平面) 在“首次可用”时单独建基线。
                if bk not in self._baseline_penetration:
                    self._baseline_penetration[bk] = depth
                baseline = self._baseline_penetration.get(bk, 0.0)
                last_depth = self._last_penetration.get(bk, depth)
                growth = depth - last_depth
                self._last_penetration[bk] = depth
                # 只有穿透量 相对基线增加 超过 margin 才告警
                # (物体被按进桌面/掉到桌面/砸到桌面)
                if depth - baseline > self.margin:
                    # 单帧深度/重定位跳变不算碰撞。真实下压/撞击在检测帧间
                    # 会继续向平面增长；要求连续两次至少增长 2mm。
                    # 仅对未夹持、正常由桌面支撑的任务物体应用；夹爪和
                    # bonded 物体的突然压入仍必须单帧立即告警。
                    _target_object = ent.category == "A_target"
                    if _target_object:
                        if growth > 0.002:
                            self._penetration_growth_streak[bk] = (
                                self._penetration_growth_streak.get(bk, 0) + 1)
                        else:
                            self._penetration_growth_streak[bk] = 0
                        if self._penetration_growth_streak[bk] < 2:
                            continue
                    delta_cm = (depth - baseline) * 100
                    self._fire(CollisionEvent(
                        frame_idx, time_sec, "penetration", eid, pe.eid,
                        {**r, "penetration_growth_m": round(growth, 4)},
                        f"实体[{ent.name}]椭球穿透平面[{pe.name}], "
                        f"侵入深度 {depth*100:.1f}cm (基线 {baseline*100:.1f}cm + 增量 {delta_cm:.1f}cm)"))
                else:
                    self._penetration_growth_streak[bk] = 0

        # ② 相交: {gripper/bonded target} × C 障碍物
        # 被持有物体往往比夹爪大得多（如水壶/茶壶），只检查夹爪
        # 会漏掉“壶身撞杯子”这类最重要的运行时碰撞。
        for eid, ell in ellipsoids.items():
            ent = graph.entities.get(eid)
            is_gripper = ent is not None and ent.category == "A_gripper"
            is_bonded_target = (ent is not None and ent.category == "A_target"
                                 and ent.bonded_to is not None)
            is_driven_target = eid in driven_targets
            if not (is_gripper or is_bonded_target or is_driven_target):
                continue
            for ob in graph.obstacles():
                ob_ell = ellipsoids.get(ob.eid)
                if ob_ell is None:
                    continue
                if self.check_whitelist(eid, ob.eid, graph):
                    continue
                if trigger_intersection(ell, ob_ell, shrink=self.shrink):
                    gap = float(np.linalg.norm(ell.center - ob_ell.center))
                    self._fire(CollisionEvent(
                        frame_idx, time_sec, "intersection", eid, ob.eid,
                        {"center_gap": round(gap, 3),
                         "motion_state": ("bonded" if is_bonded_target
                                          else "driven" if is_driven_target
                                          else "gripper")},
                        f"活动实体[{ent.name}]({('BONDED' if is_bonded_target else 'DRIVEN' if is_driven_target else 'GRIPPER')})"
                        f"椭球与障碍物[{ob.name}]椭球相交 "
                        f"(中心距 {gap*100:.0f}cm)"))

        # ②b TaskGraph 禁止的 A_target ↔ A_target 刚性接触。
        # 例如被夹持水壶撞静置茶壶。实体类别相同不代表允许互相穿过；
        # 具体哪些对象对需要检查由任务语义图决定，而非在此写死名称。
        for pair in forbidden_target_pairs:
            if len(pair) != 2:
                continue
            first, second = tuple(pair)
            first_ent, second_ent = graph.entities.get(first), graph.entities.get(second)
            first_ell, second_ell = ellipsoids.get(first), ellipsoids.get(second)
            if (first_ent is None or second_ent is None
                    or first_ell is None or second_ell is None):
                continue
            first_active = first in driven_targets or first_ent.bonded_to is not None
            second_active = second in driven_targets or second_ent.bonded_to is not None
            if not (first_active or second_active):
                continue
            active, passive = ((first, second) if first_active
                               else (second, first))
            active_ell, passive_ell = ellipsoids[active], ellipsoids[passive]
            center_gap = float(np.linalg.norm(active_ell.center - passive_ell.center))
            surface_limit = (float(np.max(active_ell.radii))
                             + float(np.max(passive_ell.radii)) + 0.03)
            contact = (trigger_intersection(active_ell, passive_ell,
                                            shrink=self.shrink)
                       or center_gap <= surface_limit)
            key = tuple(sorted((active, passive)))
            onset = contact and not self._pair_contact.get(key, False)
            self._pair_contact[key] = contact
            if onset:
                self._fire(CollisionEvent(
                    frame_idx, time_sec, "intersection", active, passive,
                    {"center_gap": round(center_gap, 3),
                     "motion_state": "forbidden_target_contact",
                     "first_contact_transition": True,
                     "collision_valid": True},
                    f"活动任务实体[{graph.entities[active].name}]与独立任务实体"
                    f"[{graph.entities[passive].name}]进入任务图禁止的刚性接触候选 "
                    f"(中心距 {center_gap*100:.0f}cm)"))

        # ②c 未绑定的夹爪 ↔ 普通任务物体首次接触。
        # gripper-target 在 ACM 中允许，是因为正常抓取必然需要接触；但在
        # 尚未建立视觉确认绑定时，这个接触也可能是抓空后的磕碰或误触。
        # 因而这里只生成低先验的局部复核候选，不直接声明碰撞。正常抓取
        # 会由 VLM 根据闭合、受控随动和无冲击反应予以拒绝。
        grippers = [(eid, ell) for eid, ell in ellipsoids.items()
                    if graph.entities.get(eid) is not None
                    and graph.entities[eid].category == "A_gripper"]
        targets = (graph.targets() if hasattr(graph, "targets") else
                   [entity for entity in graph.entities.values()
                    if entity.category == "A_target"])
        for target in targets:
            if target.bonded_to is not None or "lid" in target.name.lower():
                continue
            target_ell = ellipsoids.get(target.eid)
            if target_ell is None:
                continue
            old_target = self._previous_centers.get(target.eid)
            target_step = (0.0 if old_target is None else float(
                np.linalg.norm(target_ell.center - old_target)))
            for gid, gripper_ell in grippers:
                key = (gid, target.eid)
                center_gap = float(np.linalg.norm(
                    gripper_ell.center - target_ell.center))
                surface_limit = (float(np.max(gripper_ell.radii))
                                 + float(np.max(target_ell.radii)) + 0.025)
                contact = (trigger_intersection(
                    gripper_ell, target_ell, shrink=self.shrink)
                    or center_gap <= surface_limit)
                old_gripper = self._previous_centers.get(gid)
                previous_gap = (None if old_gripper is None or old_target is None
                                else float(np.linalg.norm(
                                    old_gripper - old_target)))
                closing = (0.0 if previous_gap is None else
                           max(0.0, previous_gap - center_gap))
                onset = contact and not self._unbound_pair_contact.get(key, False)
                # Require measured approach/reaction, or CoTracker evidence that
                # the unbound target is being driven by the gripper.  This avoids
                # firing forever on a coarse ellipsoid that starts overlapped.
                motion_supported = (closing >= 0.002 or target_step >= 0.003
                                    or target.eid in driven_targets)
                if onset and motion_supported:
                    self._fire(CollisionEvent(
                        frame_idx, time_sec, "intersection", gid, target.eid,
                        {"center_gap": round(center_gap, 4),
                         "relative_closing_m": round(closing, 4),
                         "target_reaction_m": round(target_step, 4),
                         "motion_state": "unbound_gripper_target_contact",
                         "first_contact_transition": True,
                         "collision_valid": False,
                         "needs_review": True},
                        f"未绑定夹爪[{graph.entities[gid].name}]首次接触任务物体"
                        f"[{target.name}]；可能是正常抓取，也可能是误触/磕碰，"
                        "请 VLM 检查闭合、受控随动与刚体冲击反应"))
                self._unbound_pair_contact[key] = contact

        # ③ 位移: C 类中心漂移
        for ob in graph.obstacles():
            if ob.c_init is None:
                continue
            if ob.bonded_to is not None:
                continue  # 被抓取/搬运中, 预期移动
            c_now = ellipsoids[ob.eid].center if ob.eid in ellipsoids else None
            if c_now is None:
                continue
            _arm_near = False
            for _gid, _g_ell in ellipsoids.items():
                if not _gid.startswith("gripper:"):
                    continue
                if np.linalg.norm(_g_ell.center - c_now) < 0.15:
                    _arm_near = True
                    break
            if _arm_near:
                continue  # 夹爪正在操作, 可能合法移动
            r = trigger_displacement(ob.c_init, c_now, thresh=self.disp_th)
            if r["triggered"]:
                # 位移是状态沿，不是周期事件。物体保持在新位置时只报告
                # 一次，回到阈值内后才允许下一次触发。
                if ob.eid in self._displaced_obstacles:
                    continue
                self._displaced_obstacles.add(ob.eid)
                self._fire(CollisionEvent(
                    frame_idx, time_sec, "displacement", "scene", ob.eid, r,
                    f"障碍物[{ob.name}]中心偏移 {r['displacement']*100:.1f}cm "
                    f"(阈值 {self.disp_th*100:.0f}cm) —— 疑似被碰倒/推走"))
            elif r["displacement"] < self.disp_th * 0.8:
                self._displaced_obstacles.discard(ob.eid)

        # ④ 夹爪直接撞击壶盖: 接触开始后，壶盖必须出现独立位移。
        # 普通 gripper-target 接触仍属于抓取白名单；这里只处理未绑定、
        # 未被夹爪同步驱动的小型 lid，避免把正常抓壶报成碰撞。
        grippers = [(eid, ell) for eid, ell in ellipsoids.items()
                    if graph.entities.get(eid) is not None
                    and graph.entities[eid].category == "A_gripper"]
        targets = (graph.targets() if hasattr(graph, "targets") else
                   [entity for entity in graph.entities.values()
                    if entity.category == "A_target"])
        for target in targets:
            if ("lid" not in target.name.lower() or target.bonded_to is not None
                    or target.eid in driven_targets):
                continue
            target_ell = ellipsoids.get(target.eid)
            if target_ell is None:
                continue
            old_center = self._previous_centers.get(target.eid)
            reaction = (0.0 if old_center is None else float(
                np.linalg.norm(target_ell.center - old_center)))
            for gid, gripper_ell in grippers:
                key = (gid, target.eid)
                # TCP 与细小壶盖的椭球都偏保守；除实体求交外允许 4cm
                # 的表面距离裕量，随后仍必须由壶盖运动反应确认因果。
                center_gap = float(np.linalg.norm(
                    gripper_ell.center - target_ell.center))
                surface_limit = (float(np.max(gripper_ell.radii))
                                 + float(np.max(target_ell.radii)) + 0.04)
                contact = (trigger_intersection(
                    gripper_ell, target_ell, shrink=self.shrink)
                    or center_gap <= surface_limit)
                if contact and not self._pair_contact.get(key, False):
                    self._pending_direct_contact[key] = time_sec
                pending_at = self._pending_direct_contact.get(key)
                if (pending_at is not None and time_sec - pending_at <= 1.0 + 1e-9
                        and 0.003 <= reaction <= 0.03):
                    self._fire(CollisionEvent(
                        frame_idx, time_sec, "intersection", gid, target.eid,
                        {"target_reaction_m": round(reaction, 4),
                         "motion_state": "direct_lid_contact"},
                        f"夹爪[{graph.entities[gid].name}]接触壶盖[{target.name}]后，"
                        f"壶盖独立位移 {reaction*100:.1f}cm"))
                    self._pending_direct_contact.pop(key, None)
                elif pending_at is not None and time_sec - pending_at > 1.0 + 1e-9:
                    self._pending_direct_contact.pop(key, None)
                self._pair_contact[key] = contact

        self._previous_centers = {
            eid: ell.center.copy() for eid, ell in ellipsoids.items()}

        return self.events[n_before:]
