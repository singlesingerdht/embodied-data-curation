"""Declared inputs and selected parameters must gate recommendation and execution identically."""
import copy
import json
from types import SimpleNamespace
import numpy as np
import pytest
from edc import open_dataset, recommend, run
from edc.algorithms.registry import AlgorithmSpec, Registry
from edc.algorithms.contracts import resolve_parameters
from edc.algorithms.quality.adapters.cpu import builtins, preflight


def entry(pool, ident):
    return next(x for x in pool['episodes'][0]['algorithms'] if x['algorithm']['id'] == ident)


def fixture(tmp_path):
    import h5py
    p = tmp_path / 'source.h5'
    with h5py.File(p, 'w') as f:
        f['time'] = np.array([100,110,110,120,120,120], dtype=np.int64)
        f['left'] = np.arange(6, dtype=np.float64).reshape(6,1)
    profile = {'schema_version': 1, 'id': 'fixture', 'version': '1',
        'timestamp_column': 'time', 'timestamp_unit': 'ms',
        'channels': {'left': {'modality': 'joint_space', 'fields': ['position'],
            'unit': 'deg', 'source_kind': 'feedback', 'calibration_status': 'confirmed',
            'time_source': 'acquisition', 'clock': 'robot', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
    return p, profile


def declared_plugin(called):
    spec = AlgorithmSpec('fixture__contract', '1', 'fixture', 'fixture', 'contract test',
        parameter_schema={'type': 'object', 'additionalProperties': False, 'properties': {
            'channel': {'type': 'string', 'default': 'left'},
            'physical': {'type': 'boolean', 'default': False},
            'tolerance': {'type': 'number', 'exclusiveMinimum': 0, 'default': .1}}},
        data_requirements=(
            {'id': 'recorded', 'channel_param': 'channel', 'field': 'position', 'min_samples': 2},
            {'id': 'physics', 'channel_param': 'channel', 'field': 'position', 'unit': 'rad',
             'source_kind': 'feedback', 'calibration_status': 'confirmed', 'when': {'physical': True}}),
        input_descriptions=('Selected position field; SI units required only in physical mode.',))
    def execute(context, params):
        called.append(copy.deepcopy(params))
        return {'verdict': 'OBSERVATION', 'params': params}
    return SimpleNamespace(spec=spec, run=execute)


@pytest.mark.parametrize('params,status', [({},'completed'), ({'channel':'right'},'missing_input'),
    ({'tolerance':0},'missing_input'), ({'tolerance':float('inf')},'missing_input'),
    ({'unknown':1},'missing_input'), ({'physical':True},'completed')])
def test_parameters_and_declared_bindings_gate_both_paths(tmp_path, monkeypatch, params, status):
    source, profile = fixture(tmp_path); called=[]; plugin=declared_plugin(called)
    monkeypatch.setattr('edc.algorithms.registry.entry_points', lambda group: [
        SimpleNamespace(name=plugin.spec.id,load=lambda:plugin)] if group=='edc.algorithms' else [])
    with open_dataset(source,profile=profile) as ds:
        info=entry(recommend(ds,params={plugin.spec.id:params}),plugin.spec.id)
        assert info['status']==('runnable' if status=='completed' else status)
        if status=='completed':
            assert info['parameters']['effective']['tolerance']==.1
            assert info['requirement_checks'][0]['status']=='satisfied'
        run(ds,output=tmp_path/'out',algorithm_ids=[plugin.spec.id],params={plugin.spec.id:params})
    record=json.loads(next((tmp_path/'out/_runs').glob('*/fixture__contract/records.jsonl')).read_text())
    assert record['status']==status
    assert bool(called)==(status=='completed')
    if called: assert called[0]==info['parameters']['effective']


def test_conditional_unit_requirement_changes_pool_and_manual_blocks(tmp_path,monkeypatch):
    source,profile=fixture(tmp_path);profile['channels']['left']['unit']=None
    plugin=declared_plugin([])
    monkeypatch.setattr('edc.algorithms.registry.entry_points',lambda group:[
        SimpleNamespace(name=plugin.spec.id,load=lambda:plugin)] if group=='edc.algorithms' else [])
    with open_dataset(source,profile=profile) as ds:
        raw=entry(recommend(ds),plugin.spec.id)
        physical=entry(recommend(ds,params={plugin.spec.id:{'physical':True}}),plugin.spec.id)
    assert raw['availability']=='available'
    assert raw['requirement_checks'][1]['status']=='not_required'
    assert physical['availability']=='unavailable'
    assert 'left.position' in physical['reason']
    profile['channels']['left']['unit']='deg'
    with open_dataset(source,profile=profile) as ds:
        result=entry(recommend(ds,params={plugin.spec.id:{'physical':True}},unit_policy='manual'),plugin.spec.id)
    assert result['availability']=='unavailable'


@pytest.mark.parametrize('change',[{'min_samples':0},{'time_sources':['guessed']},{'unit':'deg'},
    {'channel_param':'missing'},{'unknown':True},{'when':{'missing':True}}])
def test_invalid_contracts_fail_registration(change):
    requirement={'id':'input','channel_param':'channel','field':'position',**change}
    spec=AlgorithmSpec('fixture__bad','1','bad','x','x',
        parameter_schema={'type':'object','properties':{'channel':{'type':'string','default':'left'}}},
        data_requirements=(requirement,))
    with pytest.raises(ValueError): Registry('edc.algorithms').register(SimpleNamespace(spec=spec,run=lambda c,p:{}))


def test_historical_parameter_definitions_do_not_inject_strings():
    spec=AlgorithmSpec('fixture','1','x','x','x',parameters={'limit':'description only'})
    assert resolve_parameters(spec,{})=={}


def test_builtin_parameter_rules_and_fixed_report_snapshot(tmp_path):
    source,profile=fixture(tmp_path)
    with open_dataset(source,profile=profile) as ds:
        pool=recommend(ds,params={'Video_Integrity':{'sample_step':1,'downscale':4}})
        video=entry(pool,'Video_Integrity')
        assert video['parameters']['effective']['sample_step']==1
        assert video['input_descriptions'] and video['environment_requirements']['dependencies']
        bad=entry(recommend(ds,params={'Video_Integrity':{'sample_step':0}}),'Video_Integrity')
        assert bad['status']=='missing_input' and bad['parameters']['status']=='invalid'
        crossed=entry(recommend(ds,params={'Dedup_Check':{'dedup_ratio_low':.2,'dedup_ratio_high':.1}}),'Dedup_Check')
        assert crossed['status']=='missing_input'
        run(ds,output=tmp_path/'out',algorithm_ids=['Dedup_Check'])
    evidence=next((tmp_path/'out/_runs').glob('*/Dedup_Check/artifacts/*/timestamp-reuse-*.csv')).read_text().splitlines()
    assert len(evidence)==4


def test_configured_thresholds_keep_default_behavior_without_silent_rescoring():
    from edc.algorithms.quality.algorithms.Dedup_Check.run_check import assess
    values=[1]*5+list(range(2,97))
    assert assess(values)['dedup']['timestamp_dedup']['severity']=='error'
    configured=assess(values,dedup_ratio_low=.1,dedup_ratio_high=.2)
    assert configured['dedup']['timestamp_dedup']['severity']=='pass'
    assert configured['dedup_parameters']['dedup_ratio_high']==.2


@pytest.mark.parametrize('schema,requirement',[
    ({'type':'integer','default':1},{'id':'x','channel_param':'choice'}),
    ({'type':'boolean','default':False},{'id':'x','channel':'left','when':{'choice':'true'}})])
def test_invalid_parameter_binding_types_fail_registration(schema,requirement):
    spec=AlgorithmSpec('fixture__bad_binding','1','bad','x','x',
        parameter_schema={'type':'object','properties':{'choice':schema}},
        data_requirements=(requirement,))
    with pytest.raises(ValueError):
        Registry('edc.algorithms').register(SimpleNamespace(spec=spec,run=lambda c,p:{}))


def test_declared_rgb_and_acquisition_time_require_actual_readable_inputs():
    from edc.algorithms.contracts import evaluate_requirements
    from edc.data.ir.dataset import Channel
    channel=Channel('camera','rgb',[],3,'source',time_source='acquisition',
                    timestamps=[1,2,float('nan')],image_available=False)
    spec=AlgorithmSpec('fixture__camera','1','camera','x','x',data_requirements=(
        {'id':'frames','channel':'camera','modality':'rgb','time_sources':['acquisition']},))
    rows=evaluate_requirements(spec,SimpleNamespace(channels={'camera':channel}),{})
    assert rows[0]['status']=='missing_input'
    assert any('readable RGB' in reason for reason in rows[0]['reasons'])
    assert any('finite' in reason for reason in rows[0]['reasons'])
    channel.image_available=True;channel.timestamps=[1,2,3]
    assert evaluate_requirements(spec,SimpleNamespace(channels={'camera':channel}),{})[0]['status']=='satisfied'
