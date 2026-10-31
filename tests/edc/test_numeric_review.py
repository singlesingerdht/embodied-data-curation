"""Author review is mandatory, content-bound, and separate from numeric evidence."""
import csv
import json
from types import SimpleNamespace
import numpy as np
import pytest
import yaml
from edc.execution.runner import run
from edc.data.ir.dataset import Channel, open_dataset
from edc.robots.profiles import load_profile
from edc.robots.numeric_review import confirm_numeric_profile, numeric_review
from edc.algorithms.quality.adapters.cpu import builtins


def profile():
    return {'schema_version': 1, 'id': 'review_robot', 'version': '1', 'channels': {
        'joint_pos': {'modality': 'joint_space', 'fields': ['position'], 'unit': 'rad',
                      'source_kind': 'feedback', 'calibration_status': 'confirmed', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}


def algorithm():
    return next(x for x in builtins() if x.spec.id == 'Numeric_Check')


def evaluate(profile, values, params=None):
    c = Channel('joint_pos', 'joint_space', ['position'], len(values), 'source.json', unit='rad',
                source_kind='feedback', calibration_status='confirmed', value_available=True, representation={'quantity': 'fixture_value', 'semantic_status': 'confirmed'})
    ep = SimpleNamespace(profile=profile, channels={'joint_pos': c}, read_signal=lambda n, f: values)
    return algorithm().run(SimpleNamespace(episode=ep), params or {})[0]


def test_unit_defaults_do_not_confirm_or_rewrite_source_metadata():
    p = profile(); p['channels']['joint_pos']['unit'] = 'unknown'
    review = numeric_review(p)
    assert review['unit_suggestions']['angle'] == 'rad'
    assert review['status'] == 'pending'
    assert p['channels']['joint_pos']['unit'] == 'unknown'
    with pytest.raises(ValueError, match='source unit'):
        confirm_numeric_profile(p, 'author')


def test_confirmation_and_parameter_changes_control_actual_algorithm_execution():
    values = np.array([[0.], [2.], [0.]])
    pending = evaluate(profile(), values)
    assert pending['finite'] and pending['nan_count'] == pending['posinf_count'] == pending['neginf_count'] == 0
    assert not pending['assessment_complete'] and not pending['physical_checks_applied']
    p = profile();p['numeric_check'] = {'thresholds': {'joint': {'vel_threshold': 5.}}}
    confirmed = confirm_numeric_profile(p, 'author')
    result = evaluate(confirmed, values)
    assert result['assessment_complete'] and result['verdict'] == 'ACCEPTABLE'
    assert result['numeric_thresholds']['joint']['vel_threshold'] == 5.
    stale = evaluate(confirmed, values, {'thresholds': {'joint': {'vel_threshold': .5}}})
    assert stale['configuration_review']['status'] == 'stale'
    assert not stale['physical_checks_applied']
    confirmed['channels']['joint_pos']['unit'] = 'deg'
    assert numeric_review(confirmed)['status'] == 'stale'


@pytest.mark.parametrize('field,kind,expected', [
    ('position','command','cmd'), ('joint_q','feedback','joint')])
def test_explicit_command_feedback_controls_detector_not_field_spelling(field, kind, expected):
    p=profile();p['channels']['joint_pos'].update(fields=[field], source_kind=kind)
    p=confirm_numeric_profile(p,'synthetic author')
    channel=Channel('joint_pos','joint_space',[field],3,'source.json',unit='rad',
        source_kind=kind,calibration_status='confirmed',value_available=True, representation={'quantity': 'fixture_value', 'semantic_status': 'confirmed'})
    ep=SimpleNamespace(profile=p,channels={'joint_pos':channel},
        read_signal=lambda n,f:np.array([[0.],[2.],[0.]]))
    result=algorithm().run(SimpleNamespace(episode=ep),{})[0]
    assert result['physical_checks_applied']
    assert result['numeric'][expected] is not None
    assert result['numeric']['joint' if expected=='cmd' else 'cmd'] is None


def test_nonfinite_kinds_positions_and_complete_csv_even_without_confirmation(tmp_path):
    source = tmp_path/'episode'; source.mkdir()
    values = [[float('nan'), float('inf'), float('-inf')]]*110
    (source/'trajectory.json').write_text(json.dumps({'data': [{'joint_pos': x} for x in values]}))
    with open_dataset(source, profile=profile()) as ds:
        run(ds, algorithm_ids=['Numeric_Check'], output=tmp_path/'out')
    record = json.loads(next((tmp_path/'out/_runs').glob('*/Numeric_Check/records.jsonl')).read_text())
    item = record['result']['reports'][0]
    assert [item[k] for k in ('nan_count','posinf_count','neginf_count')] == [110,110,110]
    assert item['nonfinite_examples'][:3] == [
        {'indices':[0,0], 'source_indices':[0,0], 'kind':'NaN'},
        {'indices':[0,1], 'source_indices':[0,1], 'kind':'+Inf'},
        {'indices':[0,2], 'source_indices':[0,2], 'kind':'-Inf'}]
    assert item['nonfinite_indices_truncated'] and len(item['nonfinite_indices']) == 100
    assert item['verdict'] == 'DO_NOT_USE' and not item['assessment_complete']
    csv_path = tmp_path/'out'/next(x for x in record['artifacts'] if x.endswith('.csv'))
    assert len(list(csv.DictReader(csv_path.open()))) == 330
    page = (tmp_path/'out/report.html').read_text()
    assert '+Inf @ [0, 1]' in page and '尚未进行参数确认' in page
    assert '完整异常位置 CSV' in page


def test_review_cannot_be_forged_with_status_flag_or_pending_calibration():
    p = profile();p['numeric_review'] = {'status':'confirmed','confirmed_by':'author'}
    assert numeric_review(p)['status'] == 'stale'
    p['channels']['joint_pos']['calibration_status'] = 'pending'
    with pytest.raises(ValueError, match='calibration_status'):
        confirm_numeric_profile(p, 'author')


def test_nonfinite_virtual_channel_maps_back_to_original_component(tmp_path):
    source=tmp_path/'episode';source.mkdir()
    (source/'trajectory.json').write_text(json.dumps({'data': [
        {'joint_pos':[0.,1.,2.,3.]}, {'joint_pos':[0.,1.,2.,float('inf')]}]}))
    p=profile()
    p['channels']['selected_arm']={'modality':'joint_space','fields':['position'],
        'select':{'channel':'joint_pos','field':'position','indices':[3,1]}}
    with open_dataset(source, profile=p) as ds:
        run(ds, algorithm_ids=['Numeric_Check'], output=tmp_path/'out')
    record=json.loads(next((tmp_path/'out/_runs').glob('*/Numeric_Check/records.jsonl')).read_text())
    result=next(x for x in record['result']['reports'] if x['input_channel']=='selected_arm')
    assert result['nonfinite_examples']==[{'indices':[1,0],'source_indices':[1,3],'kind':'+Inf'}]
    assert result['source_mapping']['channel']=='joint_pos'
    csv_path=next(x for x in record['artifacts'] if x.endswith('.csv') and
        'selected_arm' not in x and '+Inf' in (tmp_path/'out'/x).read_text() and
        '[1, 3]' in (tmp_path/'out'/x).read_text() and '[1, 0]' in (tmp_path/'out'/x).read_text())
    row=next(csv.DictReader((tmp_path/'out'/csv_path).open()))
    assert row['channel']=='joint_pos' and row['source_indices']=='[1, 3]'


@pytest.mark.parametrize('override', [{'joint':{'vel_threshold':-1}}, {'joint':{'reset_ratio':2}},
    {'joint':{'limits':[[2,1]]}}, {'cmd':{'jerk_threshold':float('inf')}}, {'typo':{}}])
def test_invalid_parameters_are_rejected_before_execution(override):
    p = profile();p['numeric_check'] = {'thresholds':override}
    with pytest.raises(ValueError): load_profile(p)


def test_cli_confirmation_writes_new_profile_without_overwriting_source(tmp_path):
    from edc.execution.cli import main
    source=tmp_path/'robot.yaml';source.write_text(yaml.safe_dump(profile()))
    before=source.read_bytes();target=tmp_path/'confirmed.yaml'
    assert main(['robot','confirm-numeric','--profile',str(source),'--reviewer','author','--output',str(target)]) == 0
    assert source.read_bytes() == before
    assert numeric_review(load_profile(target))['status'] == 'confirmed'
    assert main(['robot','confirm-numeric','--profile',str(source),'--reviewer','author','--output',str(target)]) == 2


def test_per_channel_joint_limits_apply_only_to_their_own_channel():
    """Mirrored dual arms need separate tables; a channel without a table is not range-checked."""
    p = profile()
    p['channels'] = {name: dict(profile()['channels']['joint_pos']) for name in ('left', 'right', 'other')}
    p['numeric_check'] = {'thresholds': {'joint': {'vel_threshold': 5., 'limits': {
        'left': [[-0.3, 2.0]], 'right': [[-2.0, 0.3]]}}}}
    confirmed = confirm_numeric_profile(p, 'author')
    values = np.array([[1.0], [1.0], [1.0]])
    channels = {name: Channel(name, 'joint_space', ['position'], 3, 'source.json', unit='rad',
                              source_kind='feedback', calibration_status='confirmed', value_available=True,
                              representation={'quantity': 'fixture_value', 'semantic_status': 'confirmed'})
                for name in ('left', 'right', 'other')}
    ep = SimpleNamespace(profile=confirmed, channels=channels, read_signal=lambda n, f: values)
    reports = {r['input_channel']: r for r in algorithm().run(SimpleNamespace(episode=ep), {})}
    check = lambda name: next(c for c in reports[name]['check_coverage']['checks'] if c['id'] == 'joint_limits')
    assert check('left')['outcome'] == 'no_finding' and check('left')['threshold'] == [[-0.3, 2.0]]
    assert check('right')['events'] and check('right')['threshold'] == [[-2.0, 0.3]]
    assert check('other')['status'] == 'unconfigured' and check('other')['threshold'] is None


def test_per_channel_command_limits_resolve_like_joint_limits():
    from edc.robots.numeric_review import _thresholds, channel_settings
    settings = {'thresholds': _thresholds({'command_limits': {'limits': {'cmd': [[-1e9, 1e9]] * 2}}})}
    resolve = lambda name: channel_settings(settings, SimpleNamespace(name=name))['thresholds']['command_limits']['limits']
    assert resolve('cmd') == [[-1e9, 1e9]] * 2 and resolve('other') is None
    with pytest.raises(ValueError):
        _thresholds({'command_limits': {'limits': {'cmd': [[1, 0]]}}})


def test_declared_finiteness_only_field_counts_as_complete():
    from edc.execution.completeness import report_complete
    hand = Channel('hand', 'other', ['position'], 3, 'source.json', unit='rad', source_kind='feedback',
                   calibration_status='confirmed', value_available=True,
                   representation={'quantity': 'fixture_value', 'semantic_status': 'confirmed'})
    ep = SimpleNamespace(profile=profile(), channels={'hand': hand}, read_signal=lambda n, f: np.array([[0.], [1.], [2.]]))
    run_ = lambda params: algorithm().run(SimpleNamespace(episode=ep), params)[0]
    assert not report_complete(run_({}), 'Numeric_Check')
    declared = run_({'finiteness_only': ['hand.position']})
    assert declared['verdict'] == 'ACCEPTABLE' and report_complete(declared, 'Numeric_Check')
    ep.channels = {'joint_pos': Channel('joint_pos', 'joint_space', ['position'], 3, 'source.json', unit='rad',
        source_kind='feedback', calibration_status='confirmed', value_available=True,
        representation={'quantity': 'fixture_value', 'semantic_status': 'confirmed'})}
    with pytest.raises(ValueError, match='物理检查项'):
        run_({'finiteness_only': ['joint_pos.position']})


@pytest.mark.parametrize('limits', [{}, {'left': [[2, 1]]}, {'left': []}])
def test_invalid_per_channel_joint_limits_are_rejected(limits):
    p = profile(); p['numeric_check'] = {'thresholds': {'joint': {'limits': limits}}}
    with pytest.raises(ValueError):
        load_profile(p)
def prismatic_profile(unit='m'):
    return {'schema_version': 1, 'id': 'prismatic_robot', 'version': '1', 'channels': {
        'lift': {'modality': 'joint_space', 'fields': ['position'], 'unit': unit,
                 'source_kind': 'feedback', 'calibration_status': 'confirmed',
                 'representation': {'quantity': 'length', 'semantic_status': 'confirmed'}}}}


def run_prismatic(unit='m', values=None):
    values = np.array([[0.7799], [0.7800], [0.7799]]) if values is None else values
    p = confirm_numeric_profile(prismatic_profile(unit), 'prismatic author')
    c = Channel('lift', 'joint_space', ['position'], len(values), 'source.json', unit=unit,
                source_kind='feedback', calibration_status='confirmed', value_available=True,
                representation={'quantity': 'length', 'semantic_status': 'confirmed'})
    ep = SimpleNamespace(profile=p, channels={'lift': c}, read_signal=lambda n, f: values)
    return algorithm().run(SimpleNamespace(episode=ep), {})[0]


def test_prismatic_joint_position_accepts_metres_and_labels_the_unit():
    result = run_prismatic('m')
    assert result['physical_checks_applied'] and result['assessment_complete']
    assert result['unit'] == 'm'
    assert result['numeric']['joint']['difference_unit'] == 'm'
    checks = {row['id']: row for row in result['check_coverage']['checks']}
    assert checks['joint_limits']['unit'] == 'm'
    assert checks['joint_step']['unit'] == 'm per adjacent sample'
    assert result['supplemental_checks'][0]['unit'] == 'm'


def test_prismatic_joint_position_rejects_a_non_position_dimension():
    with pytest.raises(ValueError, match='rad or m'):
        confirm_numeric_profile(prismatic_profile('N'), 'prismatic author')


def test_joint_anomaly_labels_the_declared_si_unit():
    from edc.algorithms.quality.algorithms.Numeric_Check.numeric_check import check_joint_anomaly
    result = check_joint_anomaly(np.array([[0.0], [0.5], [0.0]]), vel_threshold=0.1, unit='m')
    assert result['difference_unit'] == 'm'
    assert result['events'][0]['threshold_unit'] == 'm per adjacent sample'


def test_prismatic_quantity_rejects_an_angular_unit():
    with pytest.raises(ValueError, match='declares length'):
        confirm_numeric_profile(prismatic_profile('rad'), 'prismatic author')


def test_configuration_panel_uses_field_unit_for_joint_thresholds():
    from edc.reporting.html import configuration_panel
    panel = configuration_panel(prismatic_profile(), {})
    assert '字段单位/样本' in panel
    assert 'rad/样本' not in panel
