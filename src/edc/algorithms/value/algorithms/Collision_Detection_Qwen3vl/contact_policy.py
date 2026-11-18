"""Policy-blind visual observation -> deterministic task contact decision."""
from __future__ import annotations

from dataclasses import dataclass

from .task_graph import TaskGraph


@dataclass(frozen=True)
class ContactDecision:
    label: str
    is_collision: bool
    reason: str
    policy_disposition: str | None = None


class ContactPolicyEngine:
    """Never lets task semantics rewrite what the vision observer saw."""

    @staticmethod
    def visual_onset(observation: dict) -> bool:
        first_contact = (bool(observation.get("pre_contact_gap_visible"))
                         and bool(observation.get("contact_visible"))
                         and observation.get("first_contact_frame") is not None)
        secondary_bump = (bool(observation.get("contact_visible"))
                          and bool(observation.get("relative_reaction_visible"))
                          and observation.get("reaction_frame") is not None)
        return (bool(observation.get("subject_identity_clear"))
                and bool(observation.get("object_identity_clear"))
                and (first_contact or secondary_bump))

    def decide(self, observation: dict, graph: TaskGraph,
               subject: str, object_: str, event_detail: dict | None = None
               ) -> ContactDecision:
        if not self.visual_onset(observation):
            if (observation.get("contact_visible") is False
                    and observation.get("subject_identity_clear")
                    and observation.get("object_identity_clear")):
                return ContactDecision("Allowed Contact", False,
                                       "视觉观察未确认刚体接触")
            return ContactDecision("Inconclusive", False,
                                   "身份、首次接触或二次冲击证据不完整")

        policies = [p for p in graph.contacts
                    if p.interaction_mode == "rigid_contact"
                    and {graph.semantic_group(subject), graph.semantic_group(object_)}
                    == {graph.semantic_group(p.subject), graph.semantic_group(p.object)}]
        forbidden = next((p for p in policies if p.disposition == "forbidden"), None)
        if forbidden:
            return ContactDecision("Task Failure", True, forbidden.condition,
                                   forbidden.disposition)
        allowed = next((p for p in policies if p.disposition == "allowed"), None)
        if allowed:
            return ContactDecision("Allowed Contact", False, allowed.condition,
                                   allowed.disposition)

        review = next((p for p in policies if p.disposition == "review"), None)
        detail = event_detail or {}
        involves_gripper = ("gripper" in subject.lower()
                            or "gripper" in object_.lower()
                            or "arm" in subject.lower() or "arm" in object_.lower())
        intended_grasp_state = str(detail.get("motion_state", "")).lower() in {
            "bonded", "grasp", "intended_grasp", "grasping"}
        if review and involves_gripper and not intended_grasp_state:
            return ContactDecision(
                "Task Failure", True,
                "已确认夹爪刚体接触，但运行状态不是计划抓取/绑定",
                review.disposition)
        return ContactDecision("Inconclusive", False,
                               review.condition if review else "任务图未定义该刚体接触",
                               review.disposition if review else None)
