from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor import (
    _is_progress_recovered,
)


def test_fifth_clip_recovers_above_pre_regression_level():
    post = [0.60305501, 0.60305501, 0.64638834, 0.60305501,
            0.63072168, 0.63705501, 0.65272168]
    assert _is_progress_recovered(0.6298389, post)


def test_one_rebound_sample_does_not_count_as_recovery():
    post = [0.40, 0.61, 0.40, 0.42, 0.43, 0.44, 0.45]
    assert not _is_progress_recovered(0.60, post)


def test_two_consecutive_samples_within_tolerance_count_as_recovery():
    post = [0.40, 0.571, 0.575, 0.42, 0.43, 0.44, 0.45]
    assert _is_progress_recovered(0.60, post)


def test_tail_uses_final_subtask_progress_when_post_window_is_short():
    assert _is_progress_recovered(0.70, [0.20], final_subtask_progress=0.85)
    assert not _is_progress_recovered(0.70, [0.20], final_subtask_progress=0.79)
