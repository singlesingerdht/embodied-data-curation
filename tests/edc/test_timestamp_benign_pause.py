"""Benign system pauses and the 85/75 timestamp thresholds; fixtures are synthetic."""
from types import SimpleNamespace

import pytest

from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import (
    DEFAULT_DT_CV_NO_PENALTY, DEFAULT_DT_CV_ZERO, DEFAULT_LONG_GAP_MULTIPLE,
    DEFAULT_LONG_GAP_RATIO_WARNING, DEFAULT_LONG_GAP_RATIO_ZERO,
    _detect_pause_windows, _interval_in_pause_window, _timestamp_in_pause_interior,
    grade_from_score, stream_dt_metrics)
from edc.algorithms.quality.algorithms.Root_Cause.root_cause import (
    _benign_pause_events, _extract_gap_patterns)
from edc.algorithms.quality.algorithms.Root_Cause.run_check import assess as root_assess


def timeline(rate_hz=30.0, samples=120, pause_index=None, pause_s=0.5):
    """Evenly sampled timeline; every sample from pause_index on is delayed by pause_s."""
    base = [i / rate_hz for i in range(samples)]
    if pause_index is None:
        return base
    return [t + pause_s if i >= pause_index else t for i, t in enumerate(base)]


def dt_args():
    return SimpleNamespace(dt_cv_no_penalty=DEFAULT_DT_CV_NO_PENALTY, dt_cv_zero=DEFAULT_DT_CV_ZERO,
                           long_gap_ratio_warning=DEFAULT_LONG_GAP_RATIO_WARNING,
                           long_gap_ratio_zero=DEFAULT_LONG_GAP_RATIO_ZERO,
                           long_gap_multiple=DEFAULT_LONG_GAP_MULTIPLE,
                           absolute_error_floor_ms=0.0)


@pytest.mark.parametrize('score,grade,passed', [
    (96.0, 'S', True), (95.0, 'A', True), (90.5, 'A', True), (90.0, 'B', True),
    (85.0, 'B', True), (84.9, 'C', True), (75.0, 'C', True), (74.9, 'D', False)])
def test_grade_boundaries_follow_the_85_and_75_lines(score, grade, passed):
    assert grade_from_score(score)[:2] == (grade, passed)


def test_pause_window_needs_a_majority_of_channels():
    together = {f'/cam{i}/rgb': timeline(pause_index=60) for i in range(6)}
    windows = _detect_pause_windows(together)
    assert len(windows) == 1 and windows[0]['affected_channels'] == 6

    alone = {f'/cam{i}/rgb': timeline() for i in range(6)}
    alone['/cam0/rgb'] = timeline(pause_index=60)
    assert _detect_pause_windows(alone) == []


def test_pause_window_ignores_outages_longer_than_the_limit():
    channels = {f'/cam{i}/rgb': timeline(pause_index=60, pause_s=5.0) for i in range(6)}
    assert _detect_pause_windows(channels) == []


def test_pause_window_rejects_outages_over_2500_ms():
    channels = {f'/cam{i}/rgb': timeline(pause_index=60, pause_s=3.0) for i in range(6)}
    assert _detect_pause_windows(channels) == []


def test_cadence_metrics_exclude_intervals_inside_pause_windows():
    ts = timeline(pause_index=60, pause_s=0.6)
    window = {'start_time': ts[59], 'end_time': ts[60], 'affected_channels': 6, 'max_gap_ms': 633.3}
    raw = stream_dt_metrics(ts, dt_args())
    filtered = stream_dt_metrics(ts, dt_args(), [window])
    assert raw['long_gap_count'] == 1
    assert filtered['long_gap_count'] == 0
    # 窗口判定带 50ms 边界容差，30Hz 下相邻的 1~2 个正常间隔也会被一并排除。
    assert filtered['pause_excluded_long_gap_count'] >= 1
    assert filtered['long_gap_total_count'] == filtered['pause_excluded_long_gap_count']
    assert filtered['dt_cv'] < raw['dt_cv']


def test_pause_boundary_samples_stay_in_the_metrics():
    windows = [{'start_time': 2.0, 'end_time': 2.5, 'affected_channels': 4, 'max_gap_ms': 500.0}]
    assert _interval_in_pause_window(2.0, 2.5, windows)
    assert not _interval_in_pause_window(1.9, 2.5, windows)
    assert _timestamp_in_pause_interior(2.25, windows)
    assert not _timestamp_in_pause_interior(2.005, windows)  # 边界样本仍参与配对覆盖率


def test_benign_events_need_eight_channels_or_a_global_pattern():
    eight = {'gaps': [{'topic': f'/cam{i}/rgb'} for i in range(8)], 'max_gap_ms': 900.0}
    five = {'gaps': [{'topic': f'/cam{i}/rgb'} for i in range(5)], 'max_gap_ms': 900.0}
    assert _benign_pause_events([eight], {}) == [eight]
    assert _benign_pause_events([five], {}) == []

    mostly_long = {f'/topic{i}': {'long_gap': i < 7} for i in range(12)}
    assert _benign_pause_events([five] * 3, mostly_long) == [five] * 3


def test_periodic_gaps_inside_a_benign_window_are_explained():
    ts = [0.0]
    gap_at = []
    for step in [0.1, 0.1, 0.5] * 10:
        if step == 0.5:
            gap_at.append(len(ts) - 1)
        ts.append(ts[-1] + step)
    pattern = _extract_gap_patterns({'/cam/rgb': ts})['/cam/rgb']
    assert pattern['periodic'] and pattern['unexplained_gap_count'] > 0

    benign = [{'gaps': [{'time': ts[i], 'gap_s': ts[i + 1] - ts[i]} for i in gap_at]}]
    covered = _extract_gap_patterns({'/cam/rgb': ts}, benign)['/cam/rgb']
    assert covered['unexplained_gap_count'] == 0


def root_axes(pause_s):
    ts = timeline(samples=200, pause_index=60, pause_s=pause_s)
    names = ['/cam0/rgb', '/cam1/rgb', '/cam2/rgb', '/arm/pos', '/arm/vel', '/arm/cur',
             '/lidar/scan', '/imu/data']
    return {name: ts for name in names}


def test_root_cause_downgrades_a_benign_pause_but_keeps_a_long_outage():
    benign = root_assess(root_axes(1.0))
    assert benign['root_cause']['has_benign_pauses']
    assert benign['sub_indicators'][0]['score'] == 85
    assert '系统级暂停' in benign['sub_indicators'][0]['raw_value']

    outage = root_assess(root_axes(4.0))
    assert not outage['root_cause']['has_benign_pauses']
    assert outage['sub_indicators'][0]['score'] == 60


def test_root_cause_benign_limit_matches_the_2500_ms_window_limit():
    assert root_assess(root_axes(2.4))['root_cause']['has_benign_pauses']
    assert not root_assess(root_axes(2.8))['root_cause']['has_benign_pauses']
