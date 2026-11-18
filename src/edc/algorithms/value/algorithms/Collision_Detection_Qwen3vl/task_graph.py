"""Compile a natural-language manipulation task into validated runtime semantics.

The compiler is deliberately outside the frame loop.  A language model may propose
the graph once, while tracking/contact code only consumes this deterministic object.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable


SAFETY_EVENT_TYPES = ("collision", "spill", "hand_intervention", "lid_fall")


@dataclass(frozen=True)
class EntitySemantics:
    name: str
    category: str
    semantic_group: str
    aliases: tuple[str, ...] = ()
    component_of: str | None = None


@dataclass(frozen=True)
class ContactPolicy:
    subject: str
    object: str
    disposition: str  # allowed | forbidden | review
    condition: str = ""
    interaction_mode: str = "rigid_contact"


@dataclass
class TaskGraph:
    task: str
    entities: dict[str, EntitySemantics] = field(default_factory=dict)
    contacts: list[ContactPolicy] = field(default_factory=list)
    source: str = "conservative"
    schema_version: int = 1
    compiler_warnings: list[str] = field(default_factory=list)
    safety_rules: dict[str, str] = field(default_factory=dict)
    safety_capabilities: dict[str, dict] = field(default_factory=dict)

    @staticmethod
    def _key(value: str) -> str:
        value = value.split(":", 1)[-1].replace("_", " ").strip().lower()
        return re.sub(r"\s+", " ", value)

    def resolve(self, value: str) -> EntitySemantics | None:
        key = self._key(value)
        for entity in self.entities.values():
            names = (entity.name, *entity.aliases)
            if key in {self._key(name) for name in names}:
                return entity
        return None

    def semantic_group(self, value: str) -> str:
        entity = self.resolve(value)
        return entity.semantic_group if entity else self._key(value)

    def can_overlap(self, first: str, second: str) -> bool:
        """Whether two labels may describe overlapping *different* instances."""
        a, b = self.resolve(first), self.resolve(second)
        if a is None or b is None:
            return False
        return a.component_of == b.name or b.component_of == a.name

    def contact_policy(self, first: str, second: str,
                       interaction_mode: str | None = None) -> ContactPolicy | None:
        a, b = self.semantic_group(first), self.semantic_group(second)
        for policy in self.contacts:
            if interaction_mode and policy.interaction_mode != interaction_mode:
                continue
            x, y = self.semantic_group(policy.subject), self.semantic_group(policy.object)
            if {a, b} == {x, y}:
                return policy
        return None

    def cross_view_pairs(self) -> list[tuple[EntitySemantics, EntitySemantics,
                                             ContactPolicy]]:
        """Pairs worth verifying when the primary view loses a manipulated object.

        This replaces task-specific auxiliary-view object names.  The language
        compiler chooses the contact semantics; the runtime only admits
        target/obstacle pairs whose contact is forbidden or requires review.
        """
        result = []
        seen: set[tuple[str, str]] = set()
        for policy in self.contacts:
            first, second = self.resolve(policy.subject), self.resolve(policy.object)
            if (first is None or second is None
                    or policy.interaction_mode != "rigid_contact"
                    or policy.disposition == "allowed"):
                continue
            if first.category == "C_obstacle" and second.category == "A_target":
                first, second = second, first
            if first.category != "A_target" or second.category != "C_obstacle":
                continue
            key = (first.semantic_group, second.semantic_group)
            if key not in seen:
                seen.add(key)
                result.append((first, second, policy))
        return result

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "task": self.task,
                "source": self.source,
                "compiler_warnings": self.compiler_warnings,
                "safety_rules": self.safety_rules,
                "safety_capabilities": self.resolved_safety_capabilities(),
                "entities": [asdict(entity) for entity in self.entities.values()],
                "contacts": [asdict(policy) for policy in self.contacts]}

    def resolved_safety_capabilities(self) -> dict[str, dict]:
        """Return explicit or conservatively inferred task risk plug-ins."""
        explicit = self.safety_capabilities
        task_text = self.task.lower()
        has_fluid_transfer = any(
            policy.interaction_mode == "fluid_transfer" for policy in self.contacts)
        liquid_terms = (
            "liquid", "water", "tea", "coffee", "oil", "fluid", "pour",
            "fill", "spill", "leak", "水", "茶", "液体", "倒水", "倒茶")
        has_liquid_semantics = has_fluid_transfer or any(
            term in task_text for term in liquid_terms)
        component_entities = [entity for entity in self.entities.values()
                              if (entity.component_of and entity.name.split()[-1]
                                  in {"lid", "cap", "cover"})]
        inferred = {
            "collision": {
                "enabled": len(self.entities) >= 2,
                "reason": "multiple physical task entities can make unintended rigid contact",
                "source": "inferred"},
            "spill": {
                "enabled": has_liquid_semantics,
                "reason": ("task contains fluid transfer/liquid semantics" if has_liquid_semantics
                           else "no liquid or fluid-transfer semantics in this task"),
                "source": "inferred"},
            "hand_intervention": {
                "enabled": True,
                "reason": "human workspace entry is a general manipulation safety risk",
                "source": "inferred"},
            "lid_fall": {
                "enabled": bool(component_entities),
                "reason": ("task contains independently tracked physical components" if component_entities
                           else "no detachable component is represented in the task graph"),
                "source": "inferred"},
        }
        result = {}
        for name in SAFETY_EVENT_TYPES:
            item = explicit.get(name)
            if isinstance(item, dict) and isinstance(item.get("enabled"), bool):
                result[name] = {
                    "enabled": item["enabled"],
                    "reason": str(item.get("reason") or "task compiler decision"),
                    "source": str(item.get("source") or "task_compiler")}
            else:
                result[name] = inferred[name]
        return result

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    @classmethod
    def from_dict(cls, raw: dict, *, known_names: set[str] | None = None) -> "TaskGraph":
        if int(raw.get("schema_version", 1)) != 1:
            raise ValueError("unsupported task graph schema")
        entities: dict[str, EntitySemantics] = {}
        for item in raw.get("entities", []):
            name = cls._key(str(item["name"]))
            if not name or name in entities:
                raise ValueError(f"duplicate/empty entity: {name!r}")
            component = item.get("component_of")
            component = cls._key(component) if component else None
            entities[name] = EntitySemantics(
                name=name, category=str(item.get("category", "unknown")),
                semantic_group=cls._key(str(item.get("semantic_group", name))),
                aliases=tuple(cls._key(x) for x in item.get("aliases", [])),
                component_of=component)
        if known_names:
            missing = {cls._key(x) for x in known_names} - set(entities)
            if missing:
                raise ValueError(f"task graph omitted grounded entities: {sorted(missing)}")
        for entity in entities.values():
            if entity.component_of and entity.component_of not in entities:
                raise ValueError(f"unknown component owner: {entity.component_of}")
            if entity.component_of == entity.name:
                raise ValueError("entity cannot be its own component")
        contacts = []
        seen: dict[tuple[frozenset[str], str], str] = {}
        for item in raw.get("contacts", []):
            disposition = str(item.get("disposition", "review")).lower()
            if disposition not in {"allowed", "forbidden", "review"}:
                raise ValueError(f"invalid contact disposition: {disposition}")
            subject, object_ = cls._key(item["subject"]), cls._key(item["object"])
            if subject not in entities or object_ not in entities:
                raise ValueError("contact policy references unknown entity")
            pair = frozenset((subject, object_))
            # The same physical pair may be allowed in one task phase and
            # forbidden in another (e.g. intentional insertion vs accidental
            # strike).  Only unconditional or identically-conditioned conflicts
            # are logically contradictory.
            condition = str(item.get("condition", "")).strip()
            seen_key = (pair, condition.lower())
            if seen_key in seen and seen[seen_key] != disposition:
                raise ValueError("contradictory contact policies")
            seen[seen_key] = disposition
            mode = str(item.get("interaction_mode", "rigid_contact")).lower()
            if mode not in {"rigid_contact", "proximity", "fluid_transfer",
                            "grasp", "support", "assembly"}:
                raise ValueError(f"invalid interaction mode: {mode}")
            contacts.append(ContactPolicy(subject, object_, disposition,
                                           condition, mode))
        capabilities = {}
        for name, item in dict(raw.get("safety_capabilities") or {}).items():
            if name not in SAFETY_EVENT_TYPES or not isinstance(item, dict):
                continue
            enabled = item.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError(f"capability {name} requires boolean enabled")
            capabilities[name] = {
                "enabled": enabled, "reason": str(item.get("reason") or ""),
                "source": str(item.get("source") or "task_compiler")}
        return cls(task=str(raw.get("task", "")), entities=entities,
                   contacts=contacts, source=str(raw.get("source", "llm")),
                   compiler_warnings=list(raw.get("compiler_warnings", [])),
                   safety_rules={str(k): str(v) for k, v in
                                 dict(raw.get("safety_rules") or {}).items()
                                 if k in {"collision", "spill", "hand_intervention",
                                          "lid_fall"}},
                   safety_capabilities=capabilities)

    @classmethod
    def load(cls, path: str | Path, **kwargs) -> "TaskGraph":
        return cls.from_dict(json.loads(Path(path).read_text()), **kwargs)


_COMPONENT_WORDS = {"lid", "cap", "cover", "door", "handle", "tray"}


class TaskSemanticCompiler:
    """Validated LLM compiler with a deterministic conservative fallback."""

    def __init__(self, generate: Callable[[str], str] | None = None):
        self.generate = generate

    @staticmethod
    def prompt(task: str, items: list[dict]) -> str:
        return f"""You compile robot tasks into a machine-checkable semantic graph.
Task: {task}
Grounded entities: {json.dumps(items, ensure_ascii=False)}
Return JSON only with schema_version=1, task, entities, contacts, safety_rules,
safety_capabilities.
Each entity has name, category, semantic_group, aliases, component_of (or null).
semantic_group is an IDENTITY family for interchangeable/numbered instances
(cup1/cup2/cup3 -> cup), never a broad class such as container.
component_of means a physical part that may projectively overlap its owner; never
use it merely because two objects are near each other. Distinct containers remain
distinct instances. Each contact has subject, object, disposition,
interaction_mode (rigid_contact|proximity|fluid_transfer|grasp|support|assembly),
and condition. Allowed proximity or fluid transfer NEVER means rigid contact is
allowed: emit a separate forbidden/review rigid_contact policy when relevant.
Normal intended grasp/support/pouring proximity may be allowed; unintended rigid-body contact should be forbidden or
review. Emit at most 16 task-relevant contact policies; do not enumerate
irrelevant Cartesian pairs. Multiple policies for one pair are valid only when
their conditions clearly describe different task phases. Do not invent entities
and include every grounded entity. safety_rules must contain exactly collision,
spill, hand_intervention, lid_fall, each with a short task-specific observable
criterion. Rules are context for later observers and must never claim an event.
For safety_capabilities, include exactly collision, spill, hand_intervention and
lid_fall. Each value is {{"enabled": boolean, "reason": short string}}. Enable
spill only when liquid/material flow exists. Enable lid_fall only for a detachable
lid/cap/component whose uncontrolled fall matters. Disabled capabilities must not
be scanned merely because their generic detector exists."""

    def compile(self, task: str, items: list[dict]) -> TaskGraph:
        names = {TaskGraph._key(item["name"]) for item in items}
        if self.generate is not None:
            text = self.generate(self.prompt(task, items))
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise ValueError("task compiler did not return JSON")
            raw = json.loads(match.group(0))
            raw["source"] = "llm"
            raw = self._normalize_llm(raw)
            return TaskGraph.from_dict(raw, known_names=names)
        return self._conservative(task, items)

    @staticmethod
    def _normalize_llm(raw: dict) -> dict:
        """Repair ontology/category confusion while recording every repair."""
        raw = json.loads(json.dumps(raw))
        warnings = list(raw.get("compiler_warnings", []))
        groups: dict[str, list[dict]] = {}
        for entity in raw.get("entities", []):
            group = TaskGraph._key(entity.get("semantic_group", entity["name"]))
            groups.setdefault(group, []).append(entity)
        for group, members in groups.items():
            if len(members) < 2:
                continue
            categories = {m.get("category") for m in members}
            stems = {re.sub(r"\s*\d+$", "", TaskGraph._key(m["name"]))
                     for m in members}
            if len(stems) == 1 or categories == {"A_gripper"}:
                continue
            warnings.append(f"split broad identity group {group!r}")
            for member in members:
                name = TaskGraph._key(member["name"])
                member["semantic_group"] = re.sub(r"\s*\d+$", "", name)
        contacts = raw.get("contacts", [])
        additions = []
        for policy in contacts:
            condition = str(policy.get("condition", "")).lower()
            mode = policy.get("interaction_mode")
            if not mode:
                if policy.get("disposition") != "allowed":
                    mode = "rigid_contact"
                elif "grasp" in condition or "held" in condition:
                    mode = "grasp"
                elif "support" in condition or "placement" in condition:
                    mode = "support"
                elif "pour" in condition or "proximity" in condition:
                    mode = "proximity"
                elif "lid" in condition and policy.get("disposition") == "allowed":
                    mode = "assembly"
                else:
                    mode = "rigid_contact"
                policy["interaction_mode"] = mode
            if (policy.get("disposition") == "allowed"
                    and mode in {"proximity", "fluid_transfer"}
                    and ("without rigid" in condition or "proximity" in condition)):
                additions.append({
                    "subject": policy["subject"], "object": policy["object"],
                    "disposition": "forbidden", "interaction_mode": "rigid_contact",
                    "condition": "unintended rigid-body contact during planned proximity",
                })
                warnings.append("separated proximity from rigid contact: "
                                f"{policy['subject']} / {policy['object']}")
            elif (policy.get("disposition") == "allowed"
                  and mode in {"grasp", "assembly"}):
                additions.append({
                    "subject": policy["subject"], "object": policy["object"],
                    "disposition": "review", "interaction_mode": "rigid_contact",
                    "condition": "rigid contact is allowed only in the intended grasp/assembly state",
                })
                warnings.append("added runtime-state guard for intended contact: "
                                f"{policy['subject']} / {policy['object']}")
        contacts.extend(additions)
        raw["compiler_warnings"] = warnings
        return raw

    @staticmethod
    def _conservative(task: str, items: list[dict]) -> TaskGraph:
        normalized = [(TaskGraph._key(item["name"]), item["category"]) for item in items]
        known = {name for name, _ in normalized}
        entities = {}
        for name, category in normalized:
            tokens = name.split()
            owner = " ".join(tokens[:-1]) if tokens and tokens[-1] in _COMPONENT_WORDS else None
            if owner not in known:
                owner = None
            # Numbered instances share a family for temporal event merging only.
            group = re.sub(r"\s*\d+$", "", name)
            entities[name] = EntitySemantics(name, category, group,
                                              component_of=owner)
        contacts = []
        names = list(entities)
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                a, b = entities[first], entities[second]
                if a.component_of == b.name or b.component_of == a.name:
                    disposition = "allowed"
                    condition = "assembled/supporting contact; unexpected impact still requires review"
                elif "A_gripper" in {a.category, b.category}:
                    disposition, condition = "review", "allowed only for the intended grasp target"
                else:
                    disposition, condition = "review", "verify first contact and physical reaction"
                contacts.append(ContactPolicy(
                    first, second, disposition, condition,
                    "assembly" if disposition == "allowed" else "rigid_contact"))
        graph = TaskGraph(task, entities, contacts, source="conservative",
                         safety_rules={
            "collision": "unintended rigid contact with visible onset or reaction",
            "spill": "new liquid escapes the intended transfer path or receiver",
            "hand_intervention": "human enters the task workspace or touches an object",
            "lid_fall": "a lid loses support and passively falls to an abnormal location",
        })
        graph.safety_capabilities = graph.resolved_safety_capabilities()
        if not graph.safety_capabilities["spill"]["enabled"]:
            graph.safety_rules["spill"] = "not applicable: task has no liquid transfer"
        if not graph.safety_capabilities["lid_fall"]["enabled"]:
            graph.safety_rules["lid_fall"] = "not applicable: task has no detachable lid/cap"
        return graph


class OpenAICompatibleCompiler:
    """Small dependency-free adapter; the API key is read only from an env var."""

    def __init__(self, base_url: str, model: str,
                 api_key_env: str = "TASK_COMPILER_API_KEY", timeout: int = 240,
                 api_key: str | None = None, retries: int = 1):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key_env, self.timeout = model, api_key_env, timeout
        self.api_key, self.retries = api_key, retries

    @classmethod
    def from_private_config(cls, path: str | Path,
                            api_key_env: str = "TASK_COMPILER_API_KEY"):
        """Load a chmod-600, repository-external config without logging secrets."""
        path = Path(path)
        if path.stat().st_mode & 0o077:
            raise PermissionError(f"private API config is not mode 600: {path}")
        raw = json.loads(path.read_text())
        return cls(raw["base_url"], raw["model"], api_key_env=api_key_env,
                   api_key=raw.get("api_key"))

    def __call__(self, prompt: str) -> str:
        key = self.api_key or os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f"missing environment variable {self.api_key_env}")
        payload = json.dumps({"model": self.model, "temperature": 0,
                              "max_tokens": 3072,
                              "response_format": {"type": "json_object"},
                              "messages": [{"role": "user", "content": prompt}]})
        error = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.url, payload.encode(), {
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    data = json.loads(response.read())
                return data["choices"][0]["message"]["content"]
            except (TimeoutError, OSError) as exc:
                error = exc
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"task compiler API failed after retries: {type(error).__name__}")
