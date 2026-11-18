"""Unified four-event contract and Qwen3.8-Max safety adjudication."""
from __future__ import annotations

import base64
from io import BytesIO
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import signal
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


EVENT_TYPES = {"collision", "spill", "hand_intervention", "lid_fall"}
VERDICTS = {"confirmed", "needs_review", "rejected"}


def has_explicit_hand_object_action(evidence_chain, subtype: str = "") -> bool:
    """Return true only for an affirmed, observable hand/object action."""
    text = " ".join(str(item) for item in (evidence_chain or []))
    text = f"{text} {subtype or ''}".lower()
    phrases = (
        "touch", "makes contact", "direct contact", "in contact with", "grasp", "grip",
        "hold", "holding", "held", "pick up", "picked up", "lift",
        "reposition", "set down", "sets down", "place", "placed",
        "remove", "manipulat", "push", "pull", "displac",
        "drives the", "moving the", "moved the", "moving it",
        "being moved by", "moved by the hand",
        "接触", "触碰", "抓", "拿起", "抬起", "移动物体", "移动了",
        "重新摆放", "放置", "取下", "操作", "推动", "拉动",
    )
    negations = (
        "no ", "not ", "without ", "never ", "cannot ", "can't ",
        "doesn't ", "does not ", "未", "没有", "无",
    )
    for phrase in phrases:
        offset = 0
        while True:
            index = text.find(phrase, offset)
            if index < 0:
                break
            prefix = text[max(0, index - 48):index]
            if not any(negation in prefix for negation in negations):
                return True
            offset = index + len(phrase)
    return False


def generate_with_retries(model, prompt: str,
                          images: list[tuple[str, bytes]] | None = None,
                          attempts: int = 2) -> tuple[dict, str]:
    """Retry transient transport, timeout, and response-parse failures locally."""
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            effective_prompt = prompt
            if attempt:
                effective_prompt += (
                    "\nRETRY FORMAT REQUIREMENT: Return exactly one compact, complete "
                    "JSON object. Use only the requested keys, close every array and "
                    "object, and emit no markdown or explanatory text.")
            # Local generations occasionally hit the token boundary while
            # closing a long JSON response. Give only the retry additional
            # room, preserving the normal fast path and memory profile.
            original_limit = getattr(model, "max_new_tokens", None)
            if attempt and isinstance(original_limit, int):
                model.max_new_tokens = max(original_limit, 1536)
            try:
                return model.generate(effective_prompt, images)
            finally:
                if attempt and isinstance(original_limit, int):
                    model.max_new_tokens = original_limit
        except Exception as exc:  # model adapters intentionally share no base class
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(4.0, 1.0 * (2 ** attempt)))
    assert last_error is not None
    raise last_error


@contextmanager
def _wall_clock_deadline(seconds: int):
    """Enforce a true request deadline, not merely a socket inactivity timeout."""
    enabled = (hasattr(signal, "SIGALRM")
               and threading.current_thread() is threading.main_thread())
    if not enabled:
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def _expired(_signum, _frame):
        raise TimeoutError(f"vision request exceeded {seconds}s wall-clock deadline")

    signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, max(.001, float(seconds)))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


@dataclass(slots=True)
class SafetyCandidate:
    type: str
    start: float
    end: float
    objects: list[str] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    source: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    prior_confidence: float = 0.5
    candidate_id: str = ""
    subtype: str | None = None

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unsupported safety event: {self.type}")
        self.start, self.end = float(self.start), float(self.end)
        if self.end < self.start:
            raise ValueError("candidate end precedes start")
        self.prior_confidence = min(1.0, max(0.0, float(self.prior_confidence)))
        if not self.candidate_id:
            token = uuid.uuid5(uuid.NAMESPACE_URL,
                f"{self.type}:{self.start:.3f}:{self.end:.3f}:{','.join(self.objects)}")
            self.candidate_id = f"{self.type}_{token.hex[:12]}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SafetyVerdict:
    candidate_id: str
    verdict: str
    confidence: float
    event_type: str
    subtype: str | None = None
    objects: list[str] = field(default_factory=list)
    evidence_chain: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    safety_impact: str = ""
    review_start: float | None = None
    review_end: float | None = None
    model: str = ""
    raw_output: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"unsupported verdict: {self.verdict}")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported event type: {self.event_type}")
        self.confidence = min(1.0, max(0.0, float(self.confidence)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OpenAICompatibleVisionModel:
    """Repository-external, chmod-600 OpenAI-compatible multimodal client."""

    def __init__(self, base_url: str, model: str, api_key: str,
                 timeout: int = 600):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.timeout = model, api_key, timeout

    @classmethod
    def from_private_config(cls, path: str | Path) -> "OpenAICompatibleVisionModel":
        path = Path(path)
        if path.stat().st_mode & 0o077:
            raise PermissionError(f"private API config is not mode 600: {path}")
        raw = json.loads(path.read_text())
        return cls(raw["base_url"], raw.get("model", "qwen3.8-max"), raw["api_key"],
                   int(raw.get("timeout", 600)))

    @staticmethod
    def _json(text: str) -> dict[str, Any]:
        # Decode from each opening brace instead of greedily spanning from the
        # first brace to the last. This accepts a valid object surrounded by
        # prose/code fences or followed by another brace-containing note.
        decoder = json.JSONDecoder()
        saw_object = False
        last_error: json.JSONDecodeError | None = None
        for match in re.finditer(r"\{", text):
            saw_object = True
            try:
                value, _ = decoder.raw_decode(text, match.start())
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(value, dict):
                return value
        if not saw_object:
            raise ValueError("model response contains no JSON object")
        if last_error is not None:
            raise last_error
        raise ValueError("model response contains no JSON object")

    def generate(self, prompt: str,
                 images: list[tuple[str, bytes]] | None = None) -> tuple[dict, str]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for label, payload in images or []:
            content.append({"type": "text", "text": label})
            content.append({"type": "image_url", "image_url": {"url":
                "data:image/jpeg;base64," + base64.b64encode(payload).decode()}})
        body = json.dumps({"model": self.model, "temperature": 0,
                           "response_format": {"type": "json_object"},
                           "messages": [{"role": "user", "content": content}]})
        request = urllib.request.Request(self.url, body.encode(), {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"})
        try:
            with _wall_clock_deadline(self.timeout):
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"vision API failed: HTTPError status={exc.code}") from exc
        except (TimeoutError, OSError) as exc:
            raise RuntimeError(f"vision API failed: {type(exc).__name__}") from exc
        raw = payload["choices"][0]["message"]["content"]
        return self._json(raw), raw


class LocalQwenVisionModel:
    """In-process Qwen3-VL adapter with the same contract as the remote client.

    One instance is shared by task scanning and final adjudication, so the model
    is loaded once per pipeline process.  Images are already evidence sheets;
    no paid request or API credential is involved.
    """

    DEFAULT_MODEL = None

    def __init__(self, model_path: str | Path | None = None,
                 device_map: str = "cuda:0", max_new_tokens: int = 1024):
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
        import torch
        self.torch = torch
        self.model_path = str(model_path or self.DEFAULT_MODEL)
        self.model = self.model_path
        self.max_new_tokens = int(max_new_tokens)
        self.processor = AutoProcessor.from_pretrained(
            self.model_path, local_files_only=True)
        self._network = Qwen3VLForConditionalGeneration.from_pretrained(
            self.model_path, torch_dtype=torch.bfloat16,
            device_map=device_map, local_files_only=True).eval()

    def generate(self, prompt: str,
                 images: list[tuple[str, bytes]] | None = None) -> tuple[dict, str]:
        from PIL import Image
        content: list[dict[str, Any]] = []
        for label, payload in images or []:
            content.append({"type": "text", "text": label})
            image = Image.open(BytesIO(payload)).convert("RGB")
            content.append({"type": "image", "image": image})
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]
        rendered = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        image_inputs = [item["image"] for item in content
                        if item.get("type") == "image"]
        inputs = self.processor(
            text=[rendered], images=image_inputs or None, padding=True,
            return_tensors="pt").to(self._network.device)
        with self.torch.inference_mode():
            output = self._network.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False)
        raw = self.processor.batch_decode(
            output[:, inputs.input_ids.shape[1]:],
            skip_special_tokens=True)[0]
        evidence=getattr(self, 'evidence_dir', None)
        if evidence is not None:
            evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
            counter=getattr(self,'_evidence_counter',0);self._evidence_counter=counter+1
            dest=evidence/f'{counter:06d}';dest.mkdir()
            (dest/'prompt.txt').write_text(prompt)
            (dest/'response.txt').write_text(raw)
            labels=[]
            for index,(label,payload) in enumerate(images or []):
                name=f'{index:04d}.jpg';(dest/name).write_bytes(payload)
                labels.append({'image':name,'label':label})
            (dest/'images.json').write_text(json.dumps(labels,ensure_ascii=False,indent=2))
        return OpenAICompatibleVisionModel._json(raw), raw


_FINAL_PROMPT = """You are the final multimodal safety judge for a robot task.
TaskGraph is task context, never event evidence:
{task_graph}

Candidate generated without annotation access:
{candidate}

Evidence acquisition contract:
- candidate.evidence.geometry describes how the physical PROPOSAL was generated.
  In particular geometry.dense_temporal_sampling=false does NOT describe the
  evidence images attached to this final request.
- candidate.evidence.task_context.adjudication_evidence_protocol, when present,
  is the authoritative description of the chronological images attached to this
  request: sampling rate, covered interval, synchronized views and ROI/full-view
  scope.  Image labels also contain their exact intervals.
- Never claim "no dense sampling", "single-frame evidence", or "single
  instantaneous observation" when that protocol says dense=true.  You may still
  report a genuinely unresolved gap, occlusion, insufficient spatial resolution,
  or missing view, but name the actual remaining limitation.

Inspect all chronological multi-view evidence. Decide one of confirmed,
needs_review, rejected. Never confirm an event from candidate text alone.
- collision: require a visible gap-to-contact onset or a clearly visible rigid-body
  reaction. Geometry alone cannot confirm it. Occlusion/identity conflict means
  needs_review unless another view clearly resolves contact.
  An intended grasp is proven by its outcome, not by gripper closure or task
  intent alone.  If contact pushes, slides, tips, or displaces the target while
  it remains on its original support and no stable pickup/support transfer is
  established, classify the contact as a grasp-impact collision.  Only reject
  as intended grasp when the object is visibly secured, lifted/separated as
  appropriate, and then follows the gripper under stable support.
  Do not infer a successful pickup/placement merely from the task name, phase,
  or commanded intention.  Pickup requires the observable chain
  original-support -> secured by gripper -> leaves original support.  Placement
  requires gripper-supported before contact -> aligned target support -> release.
  If the object is already resting on the same support before approach and is
  still there afterward, but direct gripper contact visibly shifts it without
  pickup, that observed displacement is an unintended grasp-impact collision.
- spill: require a newly visible stream escaping the intended transfer path or a
  new persistent wet region temporally caused by the current action. Old wet marks,
  shadows and normal flow into the receiver are rejected. Use subtype such as
  missed_target, spout_drip, rim_overflow, vessel_leak, or unknown.
- hand_intervention: require a human hand/arm entering the task workspace or
  touching/moving a task object. Background hands and continued presence do not
  create a new event. In objects, list ONLY an object whose boundary is visibly
  touched or whose motion begins with the hand; workspace entry without visible
  contact must return objects=[]. Never enumerate nearby scene objects.
- lid_fall: require the same lid to lose support/control, follow passive motion,
  and land abnormally. Collision ejection may start laterally before descending;
  little tumbling or knob-up landing is not evidence of control. Controlled
  removal/placement requires continuously visible hand/gripper support. Broken
  identity or an invisible path means needs_review.
  A clearly airborne lid with an air gap to vessel, gripper and hand, followed
  by an abnormal rest location, confirms the fall even if the initiating touch
  was occluded; causal attribution is not required. A stable final resting pose,
  including knob-up, is the expected consequence of many falls and is NOT
  evidence of controlled placement.
  Conversely, reject the candidate when any synchronized view continuously
  resolves the same lid as seated/supported throughout the proposed transition
  and the before/after evidence shows no separation, passive descent, or
  abnormal rest. Brief blur or occlusion in other views does not make that
  directly observed negative sequence uncertain.
  When candidate.evidence.robot_state is present, use measured gripper position
  and terminal effort as independent support evidence. A near-full closure with
  near-zero load synchronized with lid separation contradicts a secured grasp;
  combined with visible lid separation and abnormal landing it confirms a
  failed pickup/drop even if the fingertip interface is occluded. Robot state
  alone cannot confirm a fall without visual lid motion.

Return JSON only:
{{"verdict":"confirmed|needs_review|rejected","confidence":0.0,
"event_type":"collision|spill|hand_intervention|lid_fall","subtype":null,
"objects":[],"evidence_chain":["before","transition","consequence"],
"limitations":[],"safety_impact":"", "review_start":0.0,"review_end":0.0}}

Evidence-chain entries MUST be concrete visual observations containing an object,
an action/state and a timestamp or named view.  Never output the literal schema
placeholders "before", "transition", or "consequence".  An empty or placeholder
evidence chain is not a valid confirmed OR rejected decision; return needs_review
instead.  Rejected collision decisions must state where a positive gap remains
visible at closest approach, or what synchronized views show no contact.
"""


class UnifiedSafetyAdjudicator:
    def __init__(self, model: OpenAICompatibleVisionModel):
        self.model = model

    def judge(self, candidate: SafetyCandidate, task_graph: dict,
              images: list[tuple[str, bytes]]) -> SafetyVerdict:
        if "vlm_scan_failure" in candidate.source:
            return SafetyVerdict(
                candidate_id=candidate.candidate_id, verdict="needs_review",
                confidence=min(candidate.prior_confidence, .35),
                event_type=candidate.type, subtype=candidate.subtype,
                objects=candidate.objects,
                limitations=["direct scan coverage unavailable; cannot upgrade without a valid scan"],
                safety_impact="interval retained as an explicit coverage gap",
                review_start=candidate.start, review_end=candidate.end,
                model=self.model.model)
        prompt = _FINAL_PROMPT.format(
            task_graph=json.dumps(task_graph, ensure_ascii=False),
            candidate=json.dumps(candidate.to_dict(), ensure_ascii=False))
        try:
            parsed, raw = generate_with_retries(self.model, prompt, images)
            event_type = str(parsed.get("event_type", candidate.type))
            if event_type != candidate.type:
                # A candidate may be rejected, but must not silently mutate into a
                # different class and bypass that class's detector.
                parsed["limitations"] = list(parsed.get("limitations") or []) + [
                    f"model proposed type {event_type}; kept candidate type {candidate.type}"]
                event_type = candidate.type
            verdict = str(parsed.get("verdict", "needs_review")).lower()
            limitations = list(parsed.get("limitations") or [])
            chain_items = list(parsed.get("evidence_chain") or [])
            placeholder_chain = (not chain_items or all(
                str(item).strip().lower() in {
                    "before", "transition", "consequence", "after"}
                for item in chain_items))
            local_backend = str(getattr(
                self.model, "model", "")).startswith("local-")
            if (local_backend and verdict in {"confirmed", "rejected"}
                    and placeholder_chain):
                verdict = "needs_review"
                parsed["confidence"] = min(
                    float(parsed.get("confidence", .35)), .35)
                limitations.append(
                    "local VLM returned no concrete timestamped visual evidence")
            # Do not convert an acknowledged visibility gap into a false-safe
            # result for an otherwise unverified active liquid transfer.
            uncertainty = json.dumps(limitations, ensure_ascii=False).lower()
            evidence_text = json.dumps(
                parsed.get("evidence_chain") or [], ensure_ascii=False).lower()
            if candidate.type == "hand_intervention":
                coarse_observation = json.dumps(
                    candidate.evidence.get("vlm_observation") or {},
                    ensure_ascii=False).lower()
                coarse_visibility = str(candidate.evidence.get(
                    "visibility", {}).get("state", "unknown")).lower()
                # Treat background presence as a state, not an intervention.
                # Count independent negative facts rather than exact phrases so
                # harmless wording changes from the coarse scanner cannot turn
                # a person at the image boundary into a hand event.
                negative_fact_groups = (
                    ("no new hand", "hands remain absent", "no hand or arm",
                     "no entry into", "does not enter", "never entering",
                     "没有新的人手", "手未进入", "未进入工作区"),
                    ("remain outside", "outside workspace", "outside the workspace",
                     "at the frame edge", "at the table edge",
                     "workspace periphery", "in the background",
                     "background-only", "保持在工作区外", "画面边缘", "背景中"),
                    ("no contact", "without contact", "does not touch",
                     "never touching", "no interaction with task objects",
                     "无接触", "没有接触", "未触碰"),
                    ("no object moved", "no object displaced",
                     "robot continues", "without interruption",
                     "物体未移动", "物体未位移", "机器人继续"),
                )
                negative_facts = sum(any(term in coarse_observation
                                         for term in group)
                                     for group in negative_fact_groups)
                coarse_subtype = str(candidate.subtype or "").lower()
                background_state = any(term in coarse_subtype for term in (
                    "background", "continued_presence", "periphery"))
                coarse_clear_negative = bool(
                    negative_facts >= 2
                    and (coarse_visibility == "clear" or background_state))
                cited_views = sum(name in evidence_text for name in (
                    "front", "left", "right", "正面", "左视角", "右视角"))
                if (local_backend and verdict == "confirmed"
                        and coarse_clear_negative and cited_views <= 1):
                    verdict = "rejected"
                    parsed["confidence"] = min(
                        float(parsed.get("confidence", .9)), .9)
                    parsed["objects"] = []
                    limitations.append(
                        "clear coarse observation says hands remained outside/absent; a contradictory single-view local claim is insufficient to confirm intervention")
                ambiguous_contact = any(term in coarse_observation for term in (
                    "contact possible", "possible contact", "possibly touch",
                    "contact or hover", "touching or hovering", "may touch",
                    "might touch", "appears to touch", "near the", "near robot",
                    "可能接触", "疑似接触", "接触或悬停", "靠近"))
                coarse_object_change = any(term in coarse_observation for term in (
                    "grasps", "gripping", "reposition", "picks up", "picked up",
                    "lifts", "lifted", "sets down", "places it", "moves the",
                    "moved the", "displaces", "displaced the", "抓取", "拿起",
                    "移动物体", "重新摆放", "位移"))
                dense_object_change = any(term in evidence_text for term in (
                    "gripping and moving", "grasps and moves", "grasping and moving",
                    "visibly moving it", "actively driving", "repositions",
                    "picks up", "picked up", "lifts it", "lifted it",
                    "抓住并移动", "人手驱动", "拿起", "重新摆放"))
                if (local_backend and verdict == "confirmed"
                        and ambiguous_contact and not coarse_object_change
                        and not dense_object_change):
                    verdict = "rejected"
                    parsed["confidence"] = min(
                        float(parsed.get("confidence", .85)), .85)
                    parsed["objects"] = []
                    limitations.append(
                        "hand proximity or possible touch without clearly resolved contact or human-driven object change does not meet the intervention definition")
                hand_subtype = str(parsed.get("subtype") or "").lower()
                visible_object_action = has_explicit_hand_object_action(
                    parsed.get("evidence_chain") or [], hand_subtype)
                if not visible_object_action:
                    parsed["objects"] = []
                    if verdict == "confirmed":
                        verdict = "needs_review"
                        parsed["confidence"] = min(
                            float(parsed.get("confidence", .5)), .5)
                        limitations.append(
                            "confirmed hand intervention requires explicit visible contact or human-driven object change; proximity, presence, or generic interaction language is insufficient")
                elif local_backend:
                    grounded_objects = []
                    for name in list(parsed.get("objects") or []):
                        semantic = str(name).split(":", 1)[-1].replace(
                            "_", " ").strip().lower()
                        compact = semantic.replace(" ", "")
                        if (semantic in evidence_text
                                or compact in evidence_text.replace(" ", "")):
                            grounded_objects.append(name)
                    parsed["objects"] = grounded_objects
            incomplete_terms = ("partial", "occlu", "little view", "limit",
                                "small drip", "reflection", "resolution",
                                "hidden", "blind", "不可见", "遮挡", "分辨率")
            visible_views = sum(name in evidence_text for name in (
                "front", "left", "right", "正面", "左视角", "右视角"))
            contained_flow = any(term in evidence_text for term in (
                "terminates inside", "inside the receiver", "intended transfer path",
                "visibly contained", "进入接收容器", "流入容器"))
            dry_after = any(term in evidence_text for term in (
                "no new persistent wet", "no new wet region", "remains dry",
                "uniformly dry", "没有新增湿区", "保持干燥"))
            multi_view_clear_negative = (visible_views >= 2
                                         and contained_flow and dry_after)
            def affirmed_evidence_phrase(term: str) -> bool:
                offset = 0
                while True:
                    index = evidence_text.find(term, offset)
                    if index < 0:
                        return False
                    prefix = evidence_text[max(0, index - 64):index]
                    # Negation is local to the current clause.  A prior
                    # timestamp may correctly say "no wet region", followed
                    # by "a new wet region appears" at the next timestamp.
                    prefix = re.split(r"[.!?;\n]", prefix)[-1]
                    if not any(neg in prefix for neg in (
                            "no ", "not ", "without ", "never ",
                            "cannot see", "not clearly", "没有", "无")):
                        return True
                    offset = index + len(term)
            timestamped_spill_chain = len(re.findall(
                r"\b\d+(?:\.\d+)?s\b", evidence_text)) >= 2
            visible_escaped_liquid = any(affirmed_evidence_phrase(term)
                for term in (
                    "stream of liquid falling outside", "stream falling outside",
                    "liquid escaping onto", "liquid lands on the cloth",
                    "escaped liquid trajectory", "液流落到容器外", "液体落到桌布"))
            new_persistent_wet_region = bool(
                any(affirmed_evidence_phrase(term) for term in (
                    "new wet region appears", "new dark wet region appears",
                    "new wet patch appears", "新增湿区出现"))
                and any(affirmed_evidence_phrase(term) for term in (
                    "wet region persists", "wet patch persists",
                    "remains visible", "湿区持续")))
            if (local_backend and candidate.type == "spill"
                    and verdict != "confirmed"
                    and float(parsed.get("confidence", 0)) >= .8
                    and timestamped_spill_chain
                    and (visible_escaped_liquid or new_persistent_wet_region)):
                verdict = "confirmed"
                limitations.append(
                    "local VLM supplied a timestamped positive escaped-liquid trajectory or dry-to-new-persistent-wet transition; normalized a contradictory conservative label")
            spill_review_subtypes = {
                "active_pour_audit", "residual_spout_drip_review",
                "rim_or_wall_splash_review", "insufficient_transfer_review",
            }
            if (candidate.type == "spill"
                    and candidate.subtype in spill_review_subtypes
                    and verdict == "rejected"
                    and not multi_view_clear_negative
                    and any(term in uncertainty for term in incomplete_terms)):
                verdict = "needs_review"
                limitations.append(
                    "active-pour audit cannot be safely rejected with incomplete observability")
            geometry = dict(candidate.evidence.get("geometry") or {})
            collision_strong = bool(
                geometry.get("first_contact_transition")
                or geometry.get("depth_contact_consistent")
                or float(geometry.get("penetration_growth_m") or 0) >= .002
                or geometry.get("dense_temporal_sampling")
                or geometry.get("occlusion_bridge")
                or geometry.get("motion_state") in {
                    "lid_motion_contact_candidate", "unverified_grasp_contact"}
                or (geometry.get("roi_xyxy")
                    and int(geometry.get("candidate_count") or 0) >= 3
                    and "sam3_2d_contact" in set(
                        geometry.get("evidence_sources") or [])))
            collision_uncertainty = (
                "cannot rule out", "cannot exclude", "occlu", "single view",
                "single face", "exact contact", "sub-millimeter", "grazing",
                "无法排除", "遮挡", "单视角", "接触点")
            collision_separation = any(term in evidence_text for term in (
                "visible separation", "clear separation", "visible gap",
                "gap remains", "gap never closes", "diverging motion",
                "持续分离", "清晰间隙", "间隙未闭合", "反向运动"))
            collision_no_reaction = any(term in evidence_text for term in (
                "no rigid-body reaction", "no object reaction", "no recoil",
                "no displacement", "no wobble", "no impact response",
                "无刚体反应", "无位移", "无晃动", "无回弹"))
            collision_large_stable_gap = (
                any(term in evidence_text for term in (
                    "gap is large and stable", "large stable gap",
                    "large visible gap", "remain far apart",
                    "大且稳定的间隙", "明显保持远离"))
                and any(term in evidence_text for term in (
                    "no gap closure", "gap never closes", "never approaches",
                    "间隙从未闭合", "未发生接近")))
            collision_interface_unresolved = any(
                term in uncertainty for term in collision_uncertainty)
            collision_interface_unresolved = (
                collision_interface_unresolved
                or any(term in evidence_text for term in (
                    "not visible", "unobservable", "contact point is hidden",
                    "cannot see the contact", "不可见", "不可观测", "接触点被遮挡")))
            safety_text = str(parsed.get("safety_impact") or "").lower()
            collision_motion_state = str(geometry.get("motion_state") or "")
            controlled_grasp_contact = bool(
                (collision_motion_state == "unverified_grasp_contact"
                 or collision_motion_state.endswith(
                     "unbound_gripper_target_contact"))
                and any(term in evidence_text + " " + safety_text for term in (
                    "intended grasp", "controlled grasp", "grasp approach",
                    "permitted task behavior", "允许的抓取", "受控抓取")))
            normalized_objects = {
                str(name).split(":", 1)[-1].replace("_", " ").strip().lower()
                for name in candidate.objects}
            allowed_rigid_modes = {"support", "assembly", "grasp"}
            allowed_task_contact = any(
                str(policy.get("disposition", "")).lower() == "allowed"
                and str(policy.get("interaction_mode", "")).lower()
                    in allowed_rigid_modes
                and {str(policy.get("subject", "")).replace("_", " ").lower(),
                     str(policy.get("object", "")).replace("_", " ").lower()}
                    <= normalized_objects
                for policy in task_graph.get("contacts", []))
            hard_contact_geometry = bool(
                geometry.get("first_contact_transition")
                or geometry.get("depth_contact_consistent")
                or float(geometry.get("penetration_growth_m") or 0) >= .002)
            allowed_nonimpact_contact = bool(
                allowed_task_contact and not hard_contact_geometry)
            controlled_follow_through = any(
                term in evidence_text for term in (
                    "controlled comotion", "controlled co-motion",
                    "moving together", "moves with the gripper",
                    "sustained grasp", "受控共动", "随夹爪运动"))
            failed_grasp_displacement = bool(
                collision_motion_state in {
                    "direct_lid_contact", "lid_motion_contact_candidate",
                    "unverified_grasp_contact"}
                and any(term in evidence_text for term in (
                    "lid displacement", "object displacement", "is displaced",
                    "was displaced", "shifted", "slides", "slid", "tips",
                    "tilts", "位移", "滑动", "倾斜"))
                and any(term in evidence_text for term in (
                    "remains seated", "still seated", "remains on",
                    "not lifted", "no lift", "no pickup", "failed grasp",
                    "仍在原支撑面", "未提起", "抓取失败"))
                and not collision_no_reaction)
            if (candidate.type == "collision" and verdict == "rejected"
                    and failed_grasp_displacement):
                verdict = "needs_review"
                limitations.append(
                    "gripper contact displaced the target without proving stable pickup/support transfer; task intent alone cannot reject a grasp-impact collision")
            if (candidate.type == "collision" and verdict == "needs_review"
                    and controlled_grasp_contact and controlled_follow_through
                    and collision_no_reaction):
                verdict = "rejected"
                limitations.append(
                    "controlled grasp semantics, follow-through and absent rigid-body reaction resolve the contact as intended")
            if (candidate.type == "collision" and verdict == "needs_review"
                    and allowed_nonimpact_contact and collision_no_reaction
                    and any(term in evidence_text for term in (
                        "visible gap", "no clear rigid-body contact",
                        "清晰间隙", "没有明确刚体接触"))):
                verdict = "rejected"
                limitations.append(
                    "task graph permits this non-impact contact and local evidence retains a visible gap/no rigid reaction")
            proposal_unobservable = bool(
                geometry.get("occlusion_bridge")
                or geometry.get("visibility") == "unobservable")
            collision_clear_negative = (
                (collision_large_stable_gap
                 or (visible_views >= 2 and collision_separation
                     and collision_no_reaction))
                and not (proposal_unobservable
                         and collision_interface_unresolved))
            if (candidate.type == "collision" and collision_strong
                    and verdict == "rejected"
                    and not collision_clear_negative
                    and not controlled_grasp_contact
                    and not allowed_nonimpact_contact
                    and collision_interface_unresolved):
                verdict = "needs_review"
                limitations.append(
                    "strong collision proposal cannot be safely rejected with an unresolved contact interface")
            if (candidate.type == "collision" and verdict == "rejected"
                    and proposal_unobservable
                    and collision_interface_unresolved
                    and not collision_clear_negative
                    and not allowed_nonimpact_contact):
                verdict = "needs_review"
                limitations.append(
                    "task object was unobservable at the proposal time; later stationary appearance cannot prove that hidden contact did not occur")
            projection_only_contact = bool(
                any(term in evidence_text + " " + uncertainty for term in (
                    "silhouette overlap", "mask overlap", "masks appear to touch",
                    "projected overlap", "轮廓重叠", "掩码重叠"))
                and collision_interface_unresolved
                and not geometry.get("depth_contact_consistent")
                and collision_no_reaction)
            if (candidate.type == "collision" and verdict == "confirmed"
                    and projection_only_contact):
                verdict = "needs_review"
                limitations.append(
                    "projected silhouette/mask overlap with an unresolved interface, no depth confirmation and no rigid-body reaction cannot confirm a 3D collision")
            # A local 8B judge may conservatively choose needs_review even after
            # it has cited a timestamped visible onset AND a rigid reaction.  At
            # that point the structured geometry and visual account agree; do
            # not require a dramatic tip or displacement for a light graze.
            local_contact_terms = (
                "makes visible contact", "visible contact", "gap closes",
                "接触", "间隙闭合")
            local_reaction_terms = (
                "pushed", "resistance", "recoil", "shift", "displac",
                "wobble", "tilt", "推", "阻力", "位移", "晃动")
            def affirmed_collision_term(term: str) -> bool:
                offset = 0
                while True:
                    index = evidence_text.find(term, offset)
                    if index < 0:
                        return False
                    prefix = evidence_text[max(0, index - 56):index]
                    if not any(neg in prefix for neg in (
                            "no ", "not ", "without ", "lack of ",
                            "no visible ", "没有", "无")):
                        return True
                    offset = index + len(term)
            timestamped_collision_chain = len(re.findall(
                r"\b\d+(?:\.\d+)?s\b", evidence_text)) >= 2
            local_grounded_light_contact = bool(
                local_backend and candidate.type == "collision"
                and verdict == "needs_review"
                and float(parsed.get("confidence", 0)) >= .7
                and geometry.get("first_contact_transition")
                and timestamped_collision_chain
                and any(x in evidence_text for x in local_contact_terms)
                and any(affirmed_collision_term(x)
                        for x in local_reaction_terms)
                and not any(x in evidence_text + " " + uncertainty for x in (
                    "identity", "wrong object", "ambiguous", "occlusion",
                    "lack of clear motion", "impossible to confirm",
                    "身份不明", "对象不明", "模糊", "遮挡")))
            if local_grounded_light_contact:
                verdict = "confirmed"
                limitations.append(
                    "local VLM cited timestamped visible onset and rigid reaction consistent with the independent first-contact transition")
            successful_gripper_pickup = bool(
                candidate.type == "collision"
                and any("gripper" in str(x).lower() for x in candidate.objects)
                and any(term in evidence_text for term in (
                    "grasped and lifted", "being grasped and lifted",
                    "secured and lifted", "carried by the gripper",
                    "抓住并提起", "由夹爪稳定提起"))
                and not any(term in evidence_text for term in (
                    "failed grasp", "not lifted", "no pickup", "mis-seated",
                    "persistent rim gap", "抓取失败", "未提起", "错位")))
            if successful_gripper_pickup:
                verdict = "rejected"
                limitations.append(
                    "visible secured pickup and lift establish intended gripper-target contact rather than collision")
            lid_uncertainty = ("occlu", "inferred", "indirect", "not directly",
                               "identity", "遮挡", "推断", "间接", "身份")
            lid_continuously_supported = any(
                term in evidence_text for term in (
                    "continuously seated", "remains continuously seated",
                    "remains seated", "still seated", "stays visibly seated",
                    "uninterrupted lid visibility", "uninterrupted support",
                    "continuously supported", "持续就位", "始终就位",
                    "持续受支撑"))
            lid_no_fall_consequence = any(
                term in evidence_text for term in (
                    "no passive descent", "no detached lid",
                    "no abnormal landing", "no abnormal rest",
                    "no airborne frame", "no lid is airborne",
                    "no visual lid motion", "no support loss", "no lid fall",
                    "correctly seated", "centered on the rim",
                    "无被动下落", "无异常落点", "未脱离"))
            lid_clear_negative = bool(
                lid_continuously_supported and lid_no_fall_consequence)
            robot_state = dict(candidate.evidence.get("robot_state") or {})
            lid_empty_closure = bool(robot_state.get(
                "empty_closure_during_lid_transition"))
            lid_visual_separation = any(term in evidence_text for term in (
                "lid separating", "lid is separating", "lid leaving",
                "leaving the teapot",
                "airborne", "ballistic", "tumbling", "independent rotation",
                "transitions from seated to resting",
                "脱离", "飞行", "翻转"))
            lid_abnormal_rest = any(term in evidence_text for term in (
                "rests stably on the cloth", "rest on the cloth",
                "beside the teapot", "abnormal rest", "落在布面", "壶旁"))
            if (candidate.type == "lid_fall" and lid_empty_closure
                    and lid_visual_separation and lid_abnormal_rest
                    and not lid_no_fall_consequence):
                verdict = "confirmed"
                limitations.append(
                    "30 Hz gripper state shows near-full empty closure with near-zero load during visually observed lid separation and abnormal landing; controlled rigid support is contradicted")
            lid_chain = list(parsed.get("evidence_chain") or [])
            lid_timestamps = re.findall(
                r"\b\d+(?:\.\d+)?s\b", " ".join(map(str, lid_chain)))
            lid_motion_specific = any(term in evidence_text for term in (
                "separat", "airborne", "ballistic", "tumbling", "rotation",
                "tilt", "descent", "settles", "landing",
                "transitions from seated to resting", "脱离", "飞行",
                "翻转", "下降", "落地"))
            lid_chain_substantive = bool(
                len(lid_chain) >= 3
                and sum(len(str(item).strip()) >= 24 for item in lid_chain) >= 3
                and len(lid_timestamps) >= 2
                and lid_motion_specific and lid_abnormal_rest)
            if (candidate.type == "lid_fall" and verdict == "confirmed"
                    and not lid_chain_substantive):
                verdict = "needs_review"
                limitations.append(
                    "confirmed lid fall lacks a substantive timestamped visible support-loss, passive-motion and abnormal-landing evidence chain")
            if (candidate.type == "lid_fall"
                    and candidate.subtype == "unobserved_support_loss"
                    and verdict == "rejected"
                    and any(term in uncertainty for term in lid_uncertainty)
                    and not lid_clear_negative):
                verdict = "needs_review"
                limitations.append(
                    "controlled lid handling cannot be concluded without continuously visible support and identity")
            return SafetyVerdict(
                candidate_id=candidate.candidate_id,
                verdict=verdict,
                confidence=float(parsed.get("confidence", 0.5)),
                event_type=event_type, subtype=parsed.get("subtype"),
                objects=list(
                    parsed.get("objects", [])
                    if candidate.type == "hand_intervention"
                    else parsed.get("objects") or candidate.objects),
                evidence_chain=list(parsed.get("evidence_chain") or []),
                limitations=limitations,
                safety_impact=str(parsed.get("safety_impact", "")),
                review_start=float(parsed.get("review_start", candidate.start)),
                review_end=float(parsed.get("review_end", candidate.end)),
                model=self.model.model, raw_output=raw)
        except Exception as exc:
            return SafetyVerdict(
                candidate_id=candidate.candidate_id, verdict="needs_review",
                confidence=min(candidate.prior_confidence, 0.35),
                event_type=candidate.type, subtype=candidate.subtype,
                objects=candidate.objects,
                limitations=[
                    f"adjudication unavailable: {type(exc).__name__}: {exc}"],
                safety_impact="candidate retained for manual review",
                review_start=candidate.start, review_end=candidate.end,
                model=self.model.model)


def load_collision_candidates(path: str | Path) -> list[SafetyCandidate]:
    """Convert physical collision segments; invalid/occluded geometry is retained
    as review evidence but can never become a geometry-only confirmation."""
    report = json.loads(Path(path).read_text())
    source_events = report.get("collision_candidates") or report.get("collision_segments") or []
    result = []
    for event in source_events:
        detail = dict(event.get("detail") or {})
        start = float(detail.get("segment_start_sec",
                                 event.get("time_sec", event.get("start", 0))))
        end = float(detail.get("segment_end_sec", event.get("end", start + .5)))
        # A lone TCP-near-obstacle trigger is only an approximate temporal
        # anchor when the rigidly carried object itself is unobservable.  The
        # TCP can cross the proximity threshold well before the object's
        # contact surface reaches the obstacle because their offset is hidden.
        # Keep the geometry timestamp, but give the visual adjudicator enough
        # forward context to observe the complete approach/contact/retreat.
        # This remains low-prior evidence and can never confirm by geometry.
        if (detail.get("motion_state") == "occluded_tcp_near_cup"
                and detail.get("visibility") == "unobservable"
                and int(detail.get("candidate_count") or 1) <= 1):
            detail["physical_segment_start_sec"] = start
            detail["physical_segment_end_sec"] = end
            start = max(0.0, start - 1.0)
            end = end + 9.0
            detail["adjudication_interval_expanded"] = True
            detail["adjudication_interval_reason"] = (
                "single unobservable TCP proximity anchor; hidden rigid-body "
                "offset can delay actual object contact")
        elif detail.get("motion_state") in {
                "direct_lid_contact", "lid_motion_contact_candidate",
                "unverified_grasp_contact"}:
            physical_end = end
            detail["physical_segment_start_sec"] = start
            detail["physical_segment_end_sec"] = physical_end
            is_direct_lid = detail.get("motion_state") == "direct_lid_contact"
            outcome_horizon = 14.0 if is_direct_lid else 8.0
            end = physical_end + outcome_horizon
            anchors = [float(x) for x in
                       detail.get("evidence_hypothesis_times_sec", [])]
            anchors.extend([physical_end + 4.0, physical_end + 8.0])
            if is_direct_lid:
                anchors.extend([physical_end + 12.0,
                                physical_end + outcome_horizon])
            detail["evidence_hypothesis_times_sec"] = sorted(set(anchors))
            detail["grasp_outcome_interval_expanded"] = True
            detail["adjudication_interval_reason"] = (
                "grasp intent must be verified from later support transfer or "
                "failed-pickup outcome, not contact onset alone")
        subject = str(event.get("subject", "unknown"))
        object_ = str(event.get("object", event.get("object_", "unknown")))
        collision_valid = detail.get("collision_valid", True) is not False
        result.append(SafetyCandidate(
            type="collision", start=start, end=max(start, end),
            objects=[subject, object_], views=list(detail.get("views") or
                                                   [detail.get("view", "front")]),
            source=["geometry", "tracking"],
            prior_confidence=.72 if collision_valid else .3,
            evidence={"geometry": detail,
                      "tracking": {"motion_state": detail.get("motion_state")},
                      "visibility": {
                          "collision_valid": collision_valid,
                          "state": detail.get("visibility", "unknown")},
                      "candidate_message": event.get("message", "")}))
    return result


def load_lid_tracking_candidates(path: str | Path,
                                 min_separation_m: float = .04,
                                 min_downward_motion_m: float = .03,
                                 confirmations: int = 3) -> list[SafetyCandidate]:
    """Generate conservative lid-fall candidates from tracked identity chains.

    The chain is only a trigger.  Controlled motion, missing identity, or absent
    table-distance evidence is retained for VLM review rather than confirmed here.
    """
    report = json.loads(Path(path).read_text())
    graph = report.get("task_graph") or {}
    entities = {str(x.get("name", "")).lower(): x for x in graph.get("entities", [])}
    samples = report.get("target_state_samples") or []
    by_name: dict[str, list[dict]] = {}
    for sample in samples:
        by_name.setdefault(str(sample.get("name", "")).lower(), []).append(sample)
    candidates = []
    for lid_name, semantic in entities.items():
        owner = semantic.get("component_of")
        if "lid" not in lid_name or not owner or lid_name not in by_name or owner not in by_name:
            continue
        lid_samples = sorted(by_name[lid_name], key=lambda x: x["time_sec"])
        owner_by_frame = {x["frame"]: x for x in by_name[owner]}
        aligned = [(lid, owner_by_frame[lid["frame"]]) for lid in lid_samples
                   if lid["frame"] in owner_by_frame]
        if len(aligned) < confirmations + 1:
            continue
        import numpy as np
        baseline = np.median([
            np.linalg.norm(np.asarray(lid["center"]) - np.asarray(body["center"]))
            for lid, body in aligned[:confirmations]])
        streak = []
        for sample_index, (lid, body) in enumerate(aligned):
            separation = float(np.linalg.norm(np.asarray(lid["center"])
                                               - np.asarray(body["center"])))
            table_now = lid.get("table_distance_m")
            table_before = aligned[max(0, sample_index - confirmations)][0].get(
                "table_distance_m")
            downward = (None if table_now is None or table_before is None else
                        float(table_before) - float(table_now))
            valid = (separation - baseline >= min_separation_m
                     and downward is not None and downward >= min_downward_motion_m
                     and not bool(lid.get("controlled")))
            streak = streak + [(lid, separation, downward)] if valid else []
            if len(streak) == confirmations:
                first, last = streak[0][0], streak[-1][0]
                candidates.append(SafetyCandidate(
                    type="lid_fall", start=float(first["time_sec"]),
                    end=float(last["time_sec"]), objects=[lid_name, owner],
                    views=["front"], source=["tracking"], prior_confidence=.62,
                    evidence={"tracking": {
                        "state_chain": ["supported", "separated", "downward"],
                        "separation_growth_m": round(streak[-1][1] - baseline, 4),
                        "downward_motion_m": round(streak[-1][2], 4),
                        "confirmation_frames": confirmations,
                        "identity": lid_name},
                        "visibility": {"collision_valid": bool(
                            last.get("collision_valid", False))}}))
                streak = []
    return candidates


def load_support_reappearance_candidates(path: str | Path,
                                         min_jump_m: float = .04,
                                         max_table_distance_m: float = .03,
                                         cooldown_seconds: float = 10.0
                                         ) -> list[SafetyCandidate]:
    """Propose, never confirm, a hidden hard landing near a support surface.

    A frozen-to-reidentified center jump is not physical motion.  It only opens a
    local visual review window when a task object reappears close to the table.
    """
    report = json.loads(Path(path).read_text())
    by_name: dict[str, list[dict]] = {}
    for sample in report.get("target_state_samples") or []:
        name = str(sample.get("name", "")).lower()
        if "lid" in name:
            continue
        by_name.setdefault(name, []).append(sample)
    result = []
    invalid_states = {"occluded", "reidentifying"}
    for name, samples in by_name.items():
        samples.sort(key=lambda x: float(x.get("time_sec", 0)))
        last_emitted = -float("inf")
        for previous, current in zip(samples, samples[1:]):
            if (str(previous.get("status", "")).lower() not in invalid_states
                    or str(current.get("status", "")).lower() != "tracking"
                    or bool(current.get("controlled"))):
                continue
            table_distance = current.get("table_distance_m")
            if table_distance is None or float(table_distance) > max_table_distance_m:
                continue
            try:
                import numpy as np
                jump = float(np.linalg.norm(np.asarray(current["center"], dtype=float)
                                            - np.asarray(previous["center"], dtype=float)))
            except (KeyError, TypeError, ValueError):
                continue
            time_sec = float(current.get("time_sec", 0))
            if jump < min_jump_m or time_sec - last_emitted < cooldown_seconds:
                continue
            last_emitted = time_sec
            result.append(SafetyCandidate(
                type="collision", start=max(0.0, time_sec - 2.0),
                end=time_sec + 1.0,
                objects=[str(current.get("eid", name)), "B_env:table"],
                views=["faceImg"], source=["tracking", "support_reappearance"],
                prior_confidence=.3,
                subtype="hidden_support_impact_review",
                evidence={"geometry": {
                    "motion_state": "support_reappearance",
                    "collision_valid": False, "needs_review": True,
                    "visibility": "occluded_transition",
                    "center_jump_m": round(jump, 4),
                    "table_distance_m": round(float(table_distance), 4),
                    "segment_start_sec": max(0.0, time_sec - 2.0),
                    "segment_end_sec": time_sec + 1.0,
                    "candidate_count": 1,
                    "candidate_times_sec": [time_sec],
                    "evidence_hypothesis_times_sec": [time_sec - 1.0, time_sec,
                                                       time_sec + .8],
                    "views": ["faceImg"],
                    "evidence_sources": ["support_reappearance"],
                    "occlusion_bridge": True,
                    "dense_temporal_sampling": True},
                    "tracking": {"previous_status": previous.get("status"),
                                 "current_status": current.get("status")},
                    "visibility": {"state": "occluded_transition",
                                   "collision_valid": False}}))
    return result


def merge_safety_candidates(candidates: list[SafetyCandidate], gap: float = 2.0,
                            max_duration: dict[str, float] | None = None
                            ) -> list[SafetyCandidate]:
    limits = max_duration or {}
    merged: list[SafetyCandidate] = []
    for event in sorted(candidates, key=lambda x: (x.type, x.start, x.end)):
        previous = None
        # Candidates for different object pairs can be temporally interleaved.
        # Search the current event-type group instead of comparing only with
        # the last appended item, otherwise two overlapping observations of
        # the same contact are adjudicated independently.
        for candidate in reversed(merged):
            if candidate.type != event.type:
                break
            object_compatible = (
                not candidate.objects or not event.objects
                or (set(candidate.objects) == set(event.objects)
                    if event.type == "collision"
                    else bool(set(candidate.objects) & set(event.objects))))
            if (object_compatible
                    and event.start <= candidate.end + gap
                    and event.end - candidate.start <= limits.get(
                        event.type, float("inf"))):
                previous = candidate
                break
        if previous is None:
            merged.append(SafetyCandidate(**event.to_dict()))
            continue
        previous.end = max(previous.end, event.end)
        previous.objects = list(dict.fromkeys(previous.objects + event.objects))
        previous.views = list(dict.fromkeys(previous.views + event.views))
        previous.source = list(dict.fromkeys(previous.source + event.source))
        previous.prior_confidence = max(previous.prior_confidence,
                                        event.prior_confidence)
        previous.evidence.setdefault("merged_candidates", []).append(event.to_dict())
    return merged
