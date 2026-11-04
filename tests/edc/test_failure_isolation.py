"""Synthetic failures must not conceal successful siblings or become quality passes."""
from copy import deepcopy
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from edc.algorithms.base import AlgorithmResult, Item, FAILED, NOT_APPLICABLE, decide
from edc.algorithms.quality.adapters.results import to_result, write_quality
from edc.algorithms.quality.shared.verdicts import emit
from edc.data.regenerate import regenerate
from edc.execution import batch
from edc.reporting.batch import page, summarize


def record(reports=None, status='completed', instance='episode', reason=''):
    return {'status': status, 'instance_id': instance, 'reason': reason,
            'result': {'reports': reports or []}, 'object': {'id': 'synthetic'}}


def passed(target):
    return {'edc_item': Item(target, verdict='PASS', metrics={'score': 97},
                             evidence=[{'source_row': 12}]).__dict__, 'edc_note': '原摘要'}


def broken(target, kind='processing_error'):
    key = {'processing_error': 'error', 'missing_dependency': 'dependency_missing', 'missing_input': 'need'}[kind]
    return {'input_channel': target, 'processing_status': kind, 'ir_unavailable': {key: 'DecoderError: bad frame'},
            'reasons': ['DecoderError: bad frame'], 'assessment_complete': False, 'verdict': 'NOT_APPLICABLE'}


def test_channel_errors_keep_successful_camera_evidence_without_mutating_raw():
    raw = emit('Video_Integrity', [passed('/camera/left'), broken('/camera/right')], {})
    # emit attaches a REVIEW to an unavailable report; aggregation must honor processing_status.
    raw[0] = passed('/camera/left')
    before = deepcopy(raw)
    result = to_result('Video_Integrity', [record(raw, 'processing_error')])
    good, bad = result.items
    assert good.__dict__ == before[0]['edc_item']
    assert (bad.target, bad.status, bad.verdict, bad.reason) == (
        '/camera/right', FAILED, None, 'DecoderError: bad frame')
    assert result.level == FAILED and decide([result]) == 'unassessed'
    assert '复核' not in result.note and 'DecoderError' not in result.note
    assert raw == before


@pytest.mark.parametrize('side', ['left', 'right'])
@pytest.mark.parametrize('status', ['processing_error', 'missing_dependency', 'missing_input', 'needs_calibration'])
def test_arm_record_failure_preserves_other_arm(side, status):
    other = 'right' if side == 'left' else 'left'
    good = passed(side)
    result = to_result('Action_Smoothness', [record([good], instance=side),
                       record(status=status, instance=other, reason='boom')])
    assert result.items[0].__dict__ == good['edc_item']
    assert result.items[1].target == other and result.items[1].verdict is None
    assert result.items[1].status == (NOT_APPLICABLE if status in ('missing_input', 'needs_calibration') else FAILED)


def test_all_camera_failures_keep_all_names_and_original_causes():
    raw = emit('Video_Integrity', [broken(f'/camera/{i}') for i in range(4)], {})
    result = to_result('Video_Integrity', [record(raw, 'processing_error')])
    assert [i.target for i in result.items] == [f'/camera/{i}' for i in range(4)]
    assert all(i.status == FAILED and i.verdict is None for i in result.items)
    assert result.note == '没跑完4项'


@pytest.mark.parametrize('kind', ['processing_error', 'missing_dependency', 'missing_input'])
def test_per_report_failure_classification_without_edc_item_or_processing_status(kind):
    raw = broken('/camera', kind)
    del raw['processing_status']
    result = to_result('Video_Integrity', [record([raw], kind)])
    assert result.items[0].target == '/camera'
    assert result.items[0].status == (NOT_APPLICABLE if kind == 'missing_input' else FAILED)


def test_unexplained_parent_failure_after_success_cannot_be_kept():
    result = to_result('Action_Smoothness', [record([passed('left')], 'processing_error', 'right', 'worker lost')])
    assert result.items[0].verdict == 'PASS'
    assert result.items[1].target == 'right' and result.items[1].reason == 'worker lost'
    assert decide([result]) == 'unassessed'


def test_missing_result_for_one_instance_does_not_hide_other_instance():
    result = to_result('Action_Smoothness', [record([passed('left')]), record(instance='right')])
    assert result.items[0].verdict == 'PASS'
    assert result.items[1].status == FAILED and result.items[1].target == 'right'


def test_native_plugin_success_survives_other_instance_failure():
    native = AlgorithmResult('team__test', [Item('left', verdict='PASS', metrics={'n': 3})], 'native note')
    result = to_result('team__test', [record([{'edc_result': native.to_dict()}]),
                                    record(status='processing_error', instance='right', reason='boom')])
    assert result.items[0].__dict__ == native.items[0].__dict__
    assert result.items[1].status == FAILED
    assert to_result('team__test', [record([{'edc_result': native.to_dict()}])]).to_dict() == native.to_dict()


def write_summary(target, results, decision=None):
    out = Path(target) / '_runs' / 'synthetic'
    out.mkdir(parents=True)
    data = [{'episode': 'synthetic', 'decision': decision or decide(results),
             'results': [r.to_dict() for r in results]}]
    (out / 'quality.json').write_text(json.dumps(data), encoding='utf-8')


def summary_results(failure=True, worst='PASS'):
    items = [Item('left', verdict=worst, reason='measured problem' if worst != 'PASS' else '')]
    if failure:
        items.append(Item('/camera/right', FAILED, reason='DecoderError: bad frame'))
    return [AlgorithmResult('Action_Smoothness', [Item('left', verdict='PASS')]),
            AlgorithmResult('Video_Integrity', items)]


def mock_process(monkeypatch, results, code=1, payload=None):
    def run(cmd, **kwargs):
        target = Path(cmd[cmd.index('--output') + 1])
        if results is not None:
            write_summary(target, results)
        elif payload is not None:
            file = target / '_runs' / 'synthetic' / 'quality.json'
            file.parent.mkdir(parents=True)
            file.write_text(payload, encoding='utf-8')
        return CompletedProcess(cmd, code, stdout='worker log\nDecoderError: bad frame')
    monkeypatch.setattr(batch.subprocess, 'run', run)


@pytest.mark.parametrize('code', [0, 1])
@pytest.mark.parametrize('worst', ['PASS', 'EXCLUDE'])
def test_batch_keeps_all_algorithms_and_exitcode(tmp_path, monkeypatch, code, worst):
    source = tmp_path / 'synthetic.mcap'
    source.write_bytes(b'synthetic')
    results = summary_results(worst=worst)
    mock_process(monkeypatch, results, code)
    row = batch.check_one((source, None), 'robot.yaml', None, tmp_path)
    assert row['algos'] == {r.algorithm: r.to_dict() for r in results}
    assert row['decision'] == decide(results) and row['returncode'] == code
    assert batch._algorithm_errors(row)


def test_process_failure_after_valid_results_keeps_results_but_blocks_keep(tmp_path, monkeypatch):
    source = tmp_path / 'synthetic.mcap'
    source.write_bytes(b'synthetic')
    results = summary_results(failure=False)
    mock_process(monkeypatch, results, 7)
    row = batch.check_one((source, None), 'robot.yaml', None, tmp_path)
    assert row['decision'] == 'failed' and row['returncode'] == 7 and len(row['algos']) == 2
    assert '退出码 7' in row['log']
    html = page([row], {'robot': 'test', 'inputs': 'synthetic', 'finished': 'test', 'seconds': 0,
                        'jobs': 1, 'profile': 'robot.yaml'})
    assert html.count('<h2>') == 4
    assert '<table class="detail">' in html and '<pre>' in html and '退出码 7' in html


@pytest.mark.parametrize('payload', [None, '{', '[]', '{}', '[{}]',
    '[{"decision":"keep","results":[]}]',
    '[{"decision":"keep","results":[{"algorithm":"x","items":[{}]}]}]'])
def test_absent_corrupt_or_invalid_results_fail_without_crashing(tmp_path, monkeypatch, payload):
    source = tmp_path / 'synthetic.mcap'
    source.write_bytes(b'synthetic')
    mock_process(monkeypatch, None, code=0, payload=payload)
    row = batch.check_one((source, None), 'robot.yaml', None, tmp_path)
    assert row['decision'] == 'failed' and row['algos'] == {} and row['returncode'] == 0
    assert 'quality.json' in row['log']


def test_inconsistent_keep_with_failed_item_is_not_a_valid_summary(tmp_path):
    write_summary(tmp_path, summary_results(), decision='keep')
    with pytest.raises(ValueError, match='结论与算法结果不一致'):
        summarize(tmp_path)


@pytest.mark.parametrize('code,failure', [(1, True), (0, True), (7, False), (0, False)])
def test_batch_cli_failures_and_default_selection(tmp_path, monkeypatch, code, failure):
    from edc.execution.cli import main
    source = tmp_path / 'synthetic.mcap'
    source.write_bytes(b'synthetic')
    profile = tmp_path / 'robot.yaml'
    profile.write_text('{}')
    monkeypatch.setattr(batch, 'find_items', lambda *a: [(source, None)])
    monkeypatch.setattr(batch, 'robot_settings', lambda *a: ({}, {}))
    mock_process(monkeypatch, summary_results(failure=failure), code)
    output = tmp_path / 'report.html'
    status = main(['batch', str(source), '--robot', str(profile), '--jobs', '1', '--output', str(output)])
    assert status == int(bool(code or failure))
    row = json.loads(output.with_suffix('.json').read_text())['results'][0]
    assert len(row['algos']) == 2 and row['returncode'] == code
    html = output.read_text()
    assert html.count('<h2>') == 4 and '<table class="detail">' in html
    if failure:
        assert '/camera/right' in html and 'DecoderError: bad frame' in html
    if code or failure:
        selection = regenerate(output.with_suffix('.json'), tmp_path / 'selected', log=lambda *a: None)
        assert selection['selected'] == 0 and not (tmp_path / 'selected').exists()


def test_write_quality_keeps_other_algorithms_and_both_arm_results(tmp_path):
    for algo, records in {
        'Action_Smoothness': [record([passed('left')]), record([passed('right')])],
        'Video_Integrity': [record([broken('/camera/right')], 'processing_error')],
    }.items():
        folder = tmp_path / algo
        folder.mkdir()
        (folder / 'records.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
    episode, = write_quality(tmp_path, ['synthetic'])
    assert episode['decision'] == 'unassessed'
    assert [i['target'] for i in episode['results'][0]['items']] == ['left', 'right']
    assert episode['results'][1]['items'][0]['target'] == '/camera/right'


def test_normal_report_is_unchanged(tmp_path):
    results = summary_results(failure=False)
    write_summary(tmp_path, results)
    summary = summarize(tmp_path)
    assert summary == {'decision': 'keep', 'algos': {r.algorithm: r.to_dict() for r in results}}
    summary.update(name='synthetic', source='synthetic', seconds=0)
    info = {'robot': 'test', 'inputs': 'synthetic', 'finished': 'test', 'seconds': 0,
            'jobs': 1, 'profile': 'robot.yaml'}
    html = page([summary], info)
    assert html.count('<h2>') == 4 and html.count('<details') == 10
    assert '<pre>' not in html and '没跑完' not in html and '未评估完' not in html
