"""Synthetic-only regression for independent command limits and freeze evidence."""
import copy
import csv
import json
from types import SimpleNamespace

import numpy as np
import pytest

from edc.algorithms.quality.adapters.cpu import builtins
from edc.algorithms.quality.algorithms.Numeric_Check.run_check import assess
from edc.algorithms.quality.algorithms.Numeric_Check.settings import thresholds
from edc.data.ir.dataset import Channel, open_dataset
from edc.execution.runner import run
from edc.robots.numeric_review import confirm_numeric_profile, numeric_review, review_digest
from edc.workflows.gates import quality_gate
from edc.workflows.reviews import subject_id


def algorithm():
    return next(p for p in builtins() if p.spec.id == 'Numeric_Check')


def fixture(values, target='cmd_positions', confirmed=True, settings=None):
    values = np.asarray(values, dtype=float)
    field, modality, unit, kind = {
        'cmd_positions': ('position', 'joint_space', 'rad', 'command'),
        'joint_positions': ('position', 'joint_space', 'rad', 'feedback'),
        'gyro': ('ang_vel', 'imu', 'rad/s', 'feedback'),
        'accel': ('lin_acc', 'imu', 'm/s^2', 'feedback')}[target]
    metadata = {'modality': modality, 'fields': [field], 'unit': unit,
        'source_kind': kind, 'calibration_status': 'confirmed',
        'representation': {'quantity': 'synthetic_fixture', 'semantic_status': 'confirmed'}}
    configured = {'command_limits': {'limits': [[-1., 1.]] * values.shape[1]},
                  'joint': {'limits': [[-1., 1.]] * values.shape[1]}}
    for group, config in (settings or {}).items():
        configured.setdefault(group, {}).update(config)
    profile = {'schema_version': 1, 'id': 'numeric_supplement_fixture', 'version': '1',
               'channels': {'signal': metadata}, 'numeric_check': {'thresholds': configured}}
    if confirmed:
        profile = confirm_numeric_profile(profile, 'synthetic fixture owner')
    channel = Channel('signal', modality, [field], len(values), 'synthetic.json',
        unit=unit, source_kind=kind, calibration_status='confirmed',
        representation=metadata['representation'], value_available=True)
    episode = SimpleNamespace(id='e', profile=profile, channels={'signal': channel},
        selector={'episode_index': 7}, read_signal=lambda *args: values,
        inspect_recorded_signal=lambda *args: values)
    return episode, channel, values


def evaluate(episode, workdir=None, params=None):
    return algorithm().run(SimpleNamespace(episode=episode, workdir=workdir), params or {})[0]


def checks(report):
    return {check['id']: check for check in report['check_coverage']['checks']}


def record(report):
    return {'algorithm': {'id': 'Numeric_Check', 'version': 'ir-adapter-17'},
            'object': {'type': 'episode', 'id': 'e'}, 'status': 'completed',
            'result': {'reports': [report]}}


def decision(report, reviews=()):
    return quality_gate([record(report)], ['e'], 'run', reviews=reviews)['e']['decision']


@pytest.mark.parametrize('values,expected', [([[-1.], [0.], [1.]], 'keep'),
    ([[0.], [.1], [1.01]], 'exclude'), ([[-1.01], [-.1], [0.]], 'exclude')])
def test_command_limits_are_closed_independent_and_preserve_legacy_score(values, expected):
    ep, _, values = fixture(values, settings={'joint': {'limits': [[-100, 100]]}})
    before = values.copy()
    report = evaluate(ep)
    old = assess(cmd_positions=values)
    for key in ('score', 'verdict', 'passed', 'numeric', 'sub_indicators'):
        assert report[key] == old[key]
    check = checks(report)['command_limits']
    assert check['status'] == 'evaluated'
    assert check['outcome'] == ('finding' if expected == 'exclude' else 'no_finding')
    assert report['coverage_complete'] and decision(report) == expected
    assert np.array_equal(values, before)


@pytest.mark.parametrize('limits,status', [(None, 'unconfigured'),
    ([[-1, 1], [-1, 1]], 'invalid_configuration')])
def test_missing_or_mismatched_command_limits_never_borrow_feedback_limits(limits, status):
    ep, _, _ = fixture([[0.], [.1], [.2]], settings={'command_limits': {'limits': limits}})
    report = evaluate(ep)
    assert checks(report)['command_limits']['status'] == status
    assert not report['coverage_complete'] and decision(report) == 'unassessed'
    if limits is None:
        available = algorithm().availability(ep, {})
        assert available['availability'] == 'partial'
        assert any('独立限位' in text for text in available['fields'][0]['coverage_blockers'])


def test_limit_and_freeze_events_map_selected_components_and_episode_source(tmp_path):
    ep, c, _ = fixture([[0., 0.], [.1, .1], [1.1, .2], [1.1, .3], [1.1, .4]])
    c.selection = {'channel': 'raw', 'field': 'joint_q', 'indices': [3, 1]}
    c.joint_names = ['selected_3', 'selected_1']
    c.source_mapping = {'source': 'source.parquet', 'selector': {'episode_index': 12},
                        'row_mapping': 'identity'}
    report = evaluate(ep, tmp_path)
    limit = checks(report)['command_limits']
    assert limit['violations_count'] == 3
    assert [event['source_row'] for event in limit['events']] == [2, 3, 4]
    assert all(event['source_component_index'] == 3 and event['component_name'] == 'selected_3'
               for event in limit['events'])
    frozen = checks(report)['cmd_freeze']
    assert (frozen['events'][0]['start'], frozen['events'][0]['stop']) == (2, 5)
    assert frozen['events'][0]['source_component_index'] == 3
    for check in (limit, frozen):
        mapping = check['source_mapping']
        assert mapping['channel'] == 'raw' and mapping['field'] == 'joint_q'
        assert mapping['episode_selector'] == {'episode_index': 7}
        assert mapping['channel_source_mapping'] == c.source_mapping
        assert len(list(csv.DictReader((tmp_path / check['evidence_csv']).open()))) == len(check['events'])
    assert decision(report) == 'exclude'


@pytest.mark.parametrize('values,outcome,expected', [
    ([[0.], [0.], [0.], [0.]], 'observation', 'keep'),
    ([[0.], [.1], [.2], [.3]], 'no_finding', 'keep'),
    ([[0.], [.1], [.2], [.2], [.2]], 'finding', 'review'),
    ([[0.], [.1], [.2], [.2], [.2], [.3]], 'finding', 'review')])
def test_stationary_motion_and_terminal_plateau_have_distinct_interpretations(values, outcome, expected):
    ep, _, _ = fixture(values)
    report = evaluate(ep)
    freeze = checks(report)['cmd_freeze']
    assert freeze['outcome'] == outcome
    assert not freeze['proves_acquisition_failure']
    assert decision(report) == expected
    if expected == 'review':
        row = record(report)
        review = {'run_id': 'run', 'subject_id': subject_id(row, 0), 'action': 'dismissed'}
        assert decision(report, [review]) == 'keep'
        review['action'] = 'confirmed'
        assert decision(report, [review]) == 'exclude'


@pytest.mark.parametrize('target,group', [('cmd_positions', 'cmd_freeze'),
    ('joint_positions', 'joint_freeze'), ('gyro', 'gyro_freeze'), ('accel', 'accel_freeze')])
def test_each_numeric_kind_produces_independent_component_evidence(target, group):
    values = np.array([[0., 0., 0.], [.1, .1, 0.], [.2, .2, 0.], [.2, .3, 0.], [.2, .4, 0.]])
    ep, _, _ = fixture(values, target=target)
    check = checks(evaluate(ep))[group]
    assert check['status'] == 'evaluated'
    assert [event['component_index'] for event in check['events']] == [0]
    assert [event['component_index'] for event in check['observations']] == [2]


@pytest.mark.parametrize('time_source,timestamps,available', [
    ('acquisition', [10., 10.5, 11.2, 12.7, 14.9], True),
    ('container', [10., 10.5, 11.2, 12.7, 14.9], True),
    ('unknown', [0., 1., 2., 3., 4.], False),
    ('synthetic', [0., 1., 2., 3., 4.], False),
    ('playback', [0., 1., 2., 3., 4.], False),
    ('acquisition', [0., 1., 2., 2., 3.], False),
    ('acquisition', [0., 1., 2., 1., 3.], False),
    ('acquisition', [0., 1.], False),
    ('acquisition', [0., 1., 2., float('nan'), 3.], False),
    ('acquisition', None, False)])
def test_freeze_duration_uses_only_valid_channel_recorded_time(time_source, timestamps, available):
    ep, c, _ = fixture([[0.], [.1], [.2], [.2], [.2]])
    c.time_source, c.clock, c.timestamps = time_source, 'sensor_clock', timestamps
    c.time_evidence = {'timestamp_field': 'sensor_timestamp', 'timeline_scope': 'source_channel'}
    check = checks(evaluate(ep))['cmd_freeze']
    timeline, event = check['timeline'], check['events'][0]
    assert timeline['time_source'] == time_source and timeline['clock'] == 'sensor_clock'
    assert not timeline['establishes_acquisition_sync']
    assert timeline['status'] == ('available' if available else 'unavailable')
    assert (event['start'], event['stop']) == (2, 5)
    if available:
        assert event['start_time_s'] == 11.2 and event['last_time_s'] == 14.9
        assert event['duration_s'] == pytest.approx(3.7)
    else:
        assert event['duration_s'] is event['start_time_s'] is event['last_time_s'] is None


def test_episodes_are_never_joined_and_small_drift_is_not_frozen():
    first, _, _ = fixture([[0.], [.1]])
    second, _, _ = fixture([[.1], [.1]])
    for ep in (first, second):
        report = evaluate(ep)
        assert checks(report)['cmd_freeze']['status'] == 'insufficient_input'
        assert decision(report) == 'unassessed'
    ep, _, _ = fixture([[0.], [0.6e-6], [1.2e-6], [1.8e-6]])
    check = checks(evaluate(ep))['cmd_freeze']
    assert check['outcome'] == 'no_finding'


def test_near_constant_suffix_is_retained_when_earlier_sample_exceeds_window_range():
    ep, _, _ = fixture([[0.], [0.8e-6], [1.2e-6], [1.2e-6]])
    check = checks(evaluate(ep))['cmd_freeze']
    assert [(row['start'], row['stop']) for row in check['observations']] == [(1, 4)]


@pytest.mark.parametrize('kind,status', [('unconfirmed', 'pending_confirmation'),
    ('stale', 'pending_confirmation'), ('unknown_unit', 'pending_confirmation'),
    ('nonfinite', 'blocked_nonfinite'), ('short', 'insufficient_input'),
    ('count_mismatch', 'insufficient_input'), ('bad_shape', 'insufficient_input')])
def test_unusable_inputs_do_not_claim_completed_subchecks(kind, status):
    ep, c, values = fixture([[0.], [.1], [.2], [.2], [.2]], confirmed=kind != 'unconfirmed')
    params = {}
    if kind == 'stale': params = {'thresholds': {'cmd_freeze': {'min_run': 4}}}
    if kind == 'unknown_unit': c.unit = 'unknown'
    if kind == 'nonfinite': values[1, 0] = np.nan
    if kind == 'short': ep.read_signal = lambda *args: values[:2]; c.count = 2
    if kind == 'count_mismatch': c.count = 99
    if kind == 'bad_shape': ep.read_signal = lambda *args: values[:, 0]
    report = evaluate(ep, params=params)
    assert checks(report)['cmd_freeze']['status'] == status
    assert not report['coverage_complete']
    assert decision(report) == ('exclude' if kind == 'nonfinite' else 'unassessed')
    if kind in {'count_mismatch', 'bad_shape'}:
        assert checks(report)['command_difference']['status'] == 'insufficient_input'


def test_prior_confirmation_requires_explicit_review_of_added_defaults():
    ep, _, _ = fixture([[0.], [.1], [.2]])
    old_settings = copy.deepcopy(ep.profile['numeric_check'])
    for group in ('command_limits', 'joint_freeze', 'cmd_freeze', 'gyro_freeze', 'accel_freeze'):
        old_settings['thresholds'].pop(group)
    ep.profile['numeric_check'] = old_settings
    ep.profile['numeric_review']['configuration_sha256'] = review_digest(ep.profile, old_settings)
    assert numeric_review(ep.profile)['status'] == 'stale'
    assert not evaluate(ep)['physical_checks_applied']
    confirmed = confirm_numeric_profile(ep.profile, 'synthetic fixture owner')
    assert numeric_review(confirmed)['status'] == 'confirmed'
    assert numeric_review(confirmed, {'thresholds': {'command_limits': {'limits': [[-2, 2]]}}})['status'] == 'stale'


@pytest.mark.parametrize('override', [{'command_limits': {'limits': [[1, 0]]}},
    {'command_limits': {'limits': [[0, float('inf')]]}}, {'cmd_freeze': {'min_run': True}},
    {'joint_freeze': {'freeze_eps': -1}}, {'gyro_freeze': {'lookback': 0}},
    {'accel_freeze': {'motion_threshold': 1e-7}}])
def test_invalid_supplement_thresholds_are_rejected(override):
    with pytest.raises(ValueError):
        thresholds(override)


@pytest.mark.parametrize('last,expected', [(30., 'keep'), (90., 'exclude')])
def test_real_execution_si_conversion_catalog_and_report(tmp_path, last, expected):
    from edc.reporting.html import brief
    source = tmp_path / 'episode'
    source.mkdir()
    path = source / 'trajectory.json'
    path.write_text(json.dumps({'data': [{'joint_pos': [x]} for x in (0., 15., last)]}))
    before = path.read_bytes()
    ep, _, _ = fixture([[0.], [.1], [.2]])
    p = copy.deepcopy(ep.profile)
    p['channels'] = {'joint_pos': {**p['channels']['signal'], 'unit': 'deg'}}
    p = confirm_numeric_profile(p, 'synthetic fixture owner')
    output = tmp_path / 'output'
    with open_dataset(source, profile=p) as ds:
        run(ds, algorithm_ids=['Numeric_Check'], output=output)
    row = json.loads(next((output / '_runs').glob('*/Numeric_Check/records.jsonl')).read_text())
    report = row['result']['reports'][0]
    choice = json.loads(next((output / '_runs').glob('*/selection.json')).read_text())[0]
    assert row['status'] == 'completed' and choice['decision'] == expected
    assert choice['policy_version'] == 'episode_filter_v3'
    assert report['unit'] == 'rad' and report['source_unit'] == 'deg'
    assert path.read_bytes() == before and (output / 'catalog.json').exists()
    if expected == 'exclude':
        assert report['score'] == 100 and report['verdict'] == 'ACCEPTABLE'
        assert checks(report)['command_limits']['events'][0]['value'] == pytest.approx(np.pi / 2)
        assert '独立限位' in brief(row)
        assert any(ref.endswith('.csv') and (output / ref).is_file() for ref in row['artifacts'])
        assert '指令越界，筛选排除' in (output / 'report.html').read_text()


def test_report_displays_observations_and_candidate_evidence(tmp_path):
    from edc.reporting.html import numeric_panel, configuration_panel, brief
    ep, _, _ = fixture([[0., 0.], [.1, 0.], [.2, 0.], [.2, 0.], [.2, 0.]])
    row = record(evaluate(ep, tmp_path))
    page = numeric_panel([row], tmp_path)
    assert '动作指令数值冻结候选' in page and 'source_component_index' in page
    assert '正常保持' in page and '区间 [start, stop)' in page
    assert '需要复核' in brief(row)
    assert '动作指令独立限位' in configuration_panel(ep.profile, {})
