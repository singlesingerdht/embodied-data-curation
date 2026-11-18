import json

import pytest

from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.task_graph import TaskGraph, TaskSemanticCompiler


def test_conservative_compiler_generalizes_component_overlap():
    items = [
        {"name": "storage jar", "category": "A_target"},
        {"name": "storage jar lid", "category": "A_target"},
        {"name": "blue bottle", "category": "C_obstacle"},
    ]
    graph = TaskSemanticCompiler().compile("remove the jar lid", items)
    assert graph.can_overlap("storage_jar", "storage jar lid")
    assert not graph.can_overlap("storage jar", "blue bottle")
    assert graph.semantic_group("A_target:storage_jar_lid") == "storage jar lid"


def test_numbered_instances_share_event_family_but_not_masks():
    graph = TaskSemanticCompiler().compile("move blocks", [
        {"name": "block1", "category": "A_target"},
        {"name": "block2", "category": "A_target"},
    ])
    assert graph.semantic_group("block1") == graph.semantic_group("block2")
    assert not graph.can_overlap("block1", "block2")


def test_non_liquid_task_disables_spill_and_lid_plugins():
    graph = TaskSemanticCompiler().compile("move blocks into a tray", [
        {"name": "block1", "category": "A_target"},
        {"name": "tray", "category": "C_obstacle"},
    ])
    capabilities = graph.resolved_safety_capabilities()
    assert capabilities["collision"]["enabled"]
    assert capabilities["hand_intervention"]["enabled"]
    assert not capabilities["spill"]["enabled"]
    assert not capabilities["lid_fall"]["enabled"]


def test_fluid_transfer_and_component_enable_task_plugins():
    graph = TaskGraph.from_dict({
        "task": "pour water and cover the jar",
        "entities": [
            {"name": "jug", "category": "A_target"},
            {"name": "jar", "category": "C_obstacle"},
            {"name": "jar lid", "category": "A_target",
             "component_of": "jar"},
        ],
        "contacts": [{"subject": "jug", "object": "jar",
                      "disposition": "allowed",
                      "interaction_mode": "fluid_transfer"}],
    })
    capabilities = graph.resolved_safety_capabilities()
    assert capabilities["spill"]["enabled"]
    assert capabilities["lid_fall"]["enabled"]


def test_explicit_capability_decision_overrides_inference():
    graph = TaskGraph.from_dict({
        "task": "inspect a sealed water bottle",
        "entities": [{"name": "bottle", "category": "A_target"}],
        "safety_capabilities": {
            "spill": {"enabled": False, "reason": "sealed vessel"}},
    })
    assert not graph.resolved_safety_capabilities()["spill"]["enabled"]


def test_llm_output_is_schema_checked_and_used():
    raw = {
        "schema_version": 1,
        "task": "insert plug into socket",
        "entities": [
            {"name": "plug", "category": "A_target",
             "semantic_group": "plug", "aliases": [], "component_of": None},
            {"name": "socket", "category": "B_env",
             "semantic_group": "socket", "aliases": [], "component_of": None},
        ],
        "contacts": [{"subject": "plug", "object": "socket",
                      "disposition": "allowed", "condition": "during insertion"}],
    }
    compiler = TaskSemanticCompiler(lambda _: "```json\n" + json.dumps(raw) + "\n```")
    graph = compiler.compile("insert plug", [
        {"name": "plug", "category": "A_target"},
        {"name": "socket", "category": "B_env"},
    ])
    assert graph.source == "llm"
    assert graph.contact_policy("plug", "socket").disposition == "allowed"


def test_rejects_omitted_entity_and_contradictory_policy():
    raw = {
        "entities": [
            {"name": "part", "semantic_group": "part"},
            {"name": "fixture", "semantic_group": "fixture"},
        ],
        "contacts": [
            {"subject": "part", "object": "fixture", "disposition": "allowed"},
            {"subject": "fixture", "object": "part", "disposition": "forbidden"},
        ],
    }
    with pytest.raises(ValueError, match="contradictory"):
        TaskGraph.from_dict(raw)
    with pytest.raises(ValueError, match="omitted"):
        TaskGraph.from_dict({"entities": raw["entities"]},
                            known_names={"part", "fixture", "robot"})


def test_cross_view_pairs_are_compiled_from_roles_not_object_names():
    raw = {
        "entities": [
            {"name": "moving carton", "category": "A_target",
             "semantic_group": "carton"},
            {"name": "glass vase", "category": "C_obstacle",
             "semantic_group": "vase"},
            {"name": "carton flap", "category": "A_target",
             "semantic_group": "flap", "component_of": "moving carton"},
        ],
        "contacts": [
            {"subject": "moving carton", "object": "glass vase",
             "disposition": "forbidden"},
            {"subject": "carton flap", "object": "moving carton",
             "disposition": "allowed"},
        ],
    }
    graph = TaskGraph.from_dict(raw)
    pairs = graph.cross_view_pairs()
    assert [(a.semantic_group, b.semantic_group) for a, b, _ in pairs] == [
        ("carton", "vase")]


def test_llm_normalization_separates_class_from_identity_and_contact_mode():
    raw = {
        "entities": [
            {"name": "pouring jug", "category": "A_target",
             "semantic_group": "container"},
            {"name": "receiving cup1", "category": "C_obstacle",
             "semantic_group": "container"},
            {"name": "receiving cup2", "category": "C_obstacle",
             "semantic_group": "container"},
        ],
        "contacts": [{
            "subject": "pouring jug", "object": "receiving cup1",
            "disposition": "allowed",
            "condition": "controlled pouring proximity without rigid impact",
        }],
    }
    normalized = TaskSemanticCompiler._normalize_llm(raw)
    graph = TaskGraph.from_dict(normalized)
    assert graph.semantic_group("pouring jug") != graph.semantic_group("receiving cup1")
    assert graph.contact_policy("pouring jug", "receiving cup1",
                                "proximity").disposition == "allowed"
    assert graph.contact_policy("pouring jug", "receiving cup1",
                                "rigid_contact").disposition == "forbidden"
    assert graph.cross_view_pairs()[0][0].name == "pouring jug"
