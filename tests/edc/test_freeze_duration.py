"""Per-robot freeze_min_duration_s: a short hold after motion is a normal pause, not a review candidate."""
from types import SimpleNamespace
import numpy as np
from edc.algorithms.quality.algorithms.Numeric_Check.supplements import numeric_freeze

SETTINGS = {'min_run': 3, 'lookback': 10, 'freeze_eps': 1e-6, 'motion_threshold': 0.01}


def run(min_duration_s):
    # 100 Hz: move 1 s, hold 2 s, move 1 s, hold 6 s.
    column = np.concatenate([np.linspace(0, 1, 100), np.full(200, 1.0), np.linspace(1, 2, 100), np.full(600, 2.0)])
    times = 1773741605.0 + np.arange(len(column)) / 100
    channel = SimpleNamespace(name='cmd', count=len(column), selection=None, source='s.bag', source_mapping={},
                              time_source='acquisition', clock='robot', time_evidence={}, timestamps=list(times),
                              field_spec=lambda field: {})
    check = numeric_freeze(column[:, None], SimpleNamespace(), channel, 'position', 'cmd_positions',
                           SETTINGS, True, min_duration_s)
    return check


def test_holds_shorter_than_the_robot_setting_are_only_observations():
    strict = run(None)
    assert [round(e['duration_s']) for e in strict['events']] == [2, 6]
    relaxed = run(5)
    assert [round(e['duration_s']) for e in relaxed['events']] == [6]
    short = [e for e in relaxed['observations'] if e['type'] == 'short_hold_after_motion']
    assert [round(e['duration_s']) for e in short] == [2] and not short[0]['needs_review']
