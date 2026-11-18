"""
Test load-evidence gate in the Open branch of WindowAnalyzer.attribute().

When an Open event precedes a regression but the gripper was NOT carrying a load
(pre-Open current is not elevated above baseline), the event should be downgraded
from object_drop_by_release (severity 3, EXCLUDE) to unconfirmed_drop_by_release
(severity 2, REVIEW).
"""

import numpy as np

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor import (
    GlobalFeatures,
    WindowAnalyzer,
    detect_grip_events,
)


def _make_gfeat(n: int, baseline: float, std: float, spike_val: float = 0.0) -> GlobalFeatures:
    """Build a minimal GlobalFeatures with controllable cur_rms."""
    data = {
        "data": [
            {
                "follow_left_gripper": 0.0,
                "follow_right_gripper": 0.0,
                "follow_left_joint_cur": [baseline] * 7,
                "follow_right_joint_cur": [baseline] * 7,
                # moving end effector so the static-arm gate does not fire
                "follow_left_position": [(i % 10) * 0.01, 0.0, 0.0],
                "follow_right_position": [(i % 10) * 0.01, 0.0, 0.0],
                "follow_left_hand_effort": [0.0] * 6,
                "follow_right_hand_effort": [0.0] * 6,
            }
            for i in range(n)
        ]
    }
    g = GlobalFeatures(data, fps=20.0)
    # Inject synthetic cur_rms so we can control baseline / spike
    cur = np.full(n, baseline, dtype=np.float64)
    if spike_val > 0:
        # Put spike in the middle so it falls inside the ±50 window
        cur[n // 2 - 5 : n // 2 + 5] = spike_val
    g.cur_rms = {"left": cur.copy(), "right": cur.copy()}
    g.grip_cur_rms = {"left": cur.copy(), "right": cur.copy()}
    # Recalculate baseline / std / threshold from the injected values
    for arm in g.arms:
        g.cur_baseline[arm] = float(np.median(g.cur_rms[arm]))
        g.cur_std[arm] = float(np.std(g.cur_rms[arm]))
        g.cur_spike_threshold[arm] = g.cur_baseline[arm] + 1.5 * g.cur_std[arm]
    return g


def test_open_with_load_evidence_is_object_drop():
    """When pre-Open current is elevated (load present), keep object_drop_by_release."""
    n = 200
    baseline = 3.0
    # spike_val = 5.0  -> pre_open_cur (5.0) > baseline + 2*std (~0) => load_evidence = True
    gfeat = _make_gfeat(n, baseline=baseline, std=0.1, spike_val=5.0)
    # Create a single Open event at frame 100
    grip_events = {"left": [], "right": [(100, "open")]}
    # Progress samples: drop from 0.5 to 0.2, no recovery
    prog_samples = [
        {"frame_index": i, "progress": 0.5 if i < 100 else 0.2} for i in range(0, n, 10)
    ]
    wa = WindowAnalyzer(gfeat, grip_events, prog_samples, fps=20.0)
    result = wa.attribute(frame=100, arm="right")
    assert result.primary_label == "object_drop_by_release"
    assert result.severity == 3


def test_open_without_load_evidence_is_unconfirmed_drop():
    """When pre-Open current is at baseline and no spike (no load), downgrade to unconfirmed_drop_by_release."""
    n = 200
    baseline = 3.0
    # spike_val = 0 -> pre_open_cur stays at baseline, no spike -> load_evidence = False
    gfeat = _make_gfeat(n, baseline=baseline, std=0.1, spike_val=0.0)
    grip_events = {"left": [], "right": [(100, "open")]}
    prog_samples = [
        {"frame_index": i, "progress": 0.5 if i < 100 else 0.2} for i in range(0, n, 10)
    ]
    wa = WindowAnalyzer(gfeat, grip_events, prog_samples, fps=20.0)
    result = wa.attribute(frame=100, arm="right")
    assert result.primary_label == "unconfirmed_drop_by_release"
    assert result.severity == 2


def test_open_with_cur_spike_but_low_pre_open_is_object_drop():
    """When pre-Open current is low but window has significant spike, keep object_drop_by_release."""
    n = 200
    baseline = 3.0
    # spike_val = 5.0 -> window has high cur_max -> load_evidence = True (via spike)
    gfeat = _make_gfeat(n, baseline=baseline, std=0.1, spike_val=5.0)
    grip_events = {"left": [], "right": [(100, "open")]}
    prog_samples = [
        {"frame_index": i, "progress": 0.5 if i < 100 else 0.2} for i in range(0, n, 10)
    ]
    wa = WindowAnalyzer(gfeat, grip_events, prog_samples, fps=20.0)
    result = wa.attribute(frame=100, arm="right")
    assert result.primary_label == "object_drop_by_release"
    assert result.severity == 3
