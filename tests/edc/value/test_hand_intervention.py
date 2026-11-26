"""人手干预：候选合并、扫描过滤和复核规则（用假模型，不加载权重）。"""
import numpy as np

from edc.algorithms.value.algorithms.Hand_Intervention import method


def frames(seconds=30, fps=4):
    image = np.zeros((180, 240, 3), np.uint8)
    return {'head': [(i / fps, image) for i in range(seconds * fps)]}


def test_merge_joins_close_candidates_up_to_max_length():
    scan = {'observation': {}}
    merged = method.merge([{'start': 0, 'end': 2, 'scan': scan}, {'start': 3, 'end': 5, 'scan': scan},
                           {'start': 6, 'end': 16, 'scan': scan}, {'start': 30, 'end': 31, 'scan': scan}])
    assert [(m['start'], m['end']) for m in merged] == [(0, 5), (6, 16), (30, 31)]


def test_scan_drops_background_only_and_records_failures():
    answers = iter([
        ({'hand': 'present', 'candidates': [
            {'start': 3, 'end': 5, 'observation': {'transition': 'hand grasps the cup'}},
            {'start': 6, 'end': 7, 'observation': {'after': 'person in the background, no contact'}}]}, ''),
        (None, 'garbled'), ({'hand': 'absent', 'candidates': []}, '')])
    candidates, failures = method.scan(lambda prompt, images: next(answers), frames(), 'task', 12, 2)
    assert [(c['start'], c['end']) for c in candidates] == [(3, 5)]
    assert failures and failures[0]['start'] == 10


def review(answer):
    event = {'start': 3, 'end': 5, 'scans': [{}]}
    return method.review(lambda prompt, images: (answer, ''), frames(), 'task', event, 4)['verdict']


def test_review_needs_visible_contact_to_confirm():
    assert review({'verdict': 'confirmed', 'evidence_chain': ['head 3.5s: a human hand picks up the cup']}) == 'confirmed'
    assert review({'verdict': 'confirmed', 'evidence_chain': ['head 3.5s: a human hand enters near the cup']}) == 'needs_review'
    assert review({'verdict': 'confirmed', 'evidence_chain': ['head 3.5s: the hand does not touch the cup']}) == 'needs_review'
    assert review({'verdict': 'rejected', 'evidence_chain': []}) == 'needs_review'
    assert review(None) == 'needs_review'
