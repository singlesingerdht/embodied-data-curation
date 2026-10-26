"""The advertised plugin inputs must be the exact SI/clock inputs executed."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import numpy as np
import pytest
from edc import open_dataset, recommend, run


def mcap_fixture(tmp_path):
    path=Path(__file__).with_name('test_config_native_timeline.py')
    spec=importlib.util.spec_from_file_location('clock_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=tmp_path/'data.mcap';module.write_mcap(source)
    profile={'schema_version':1,'id':'clock_execution','version':'1','channels':{
        '/head':{'logical_name':'rgb/head','modality':'rgb','time_source':'acquisition','clock':'head_clock'},
        '/fast':{'time_source':'acquisition','clock':'arm_clock'}},
        'transforms':{'clocks':{'arm_to_head':{'source':'arm_clock','target':'head_clock',
            'scale':1.1,'offset_seconds':.25,'valid_range':[0,100],'provenance':'synthetic test mapping'}}}}
    return source,profile


@pytest.mark.parametrize('ident',['Check_Timestamp','Sync_Check','Root_Cause'])
def test_clock_mapping_consumed_by_real_cpu_method(tmp_path,monkeypatch,ident):
    source,profile=mcap_fixture(tmp_path)
    before=source.read_bytes()
    params={'channels':['rgb/head','/fast'],'target_clock':'head_clock'}
    if ident=='Sync_Check':params['primary']='rgb/head'
    seen=[]
    module=importlib.import_module(f'edc.algorithms.quality.algorithms.{ident}.run_check')
    method='assess' if ident=='Sync_Check' else 'assess_from_ir'
    original=getattr(module,method)
    def capture(*args,**kwargs):
        axes=args[0] if ident=='Sync_Check' else {n:t.timestamps for n,t in args[0].topics.items()}
        seen.append(axes)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,method,capture)
    with open_dataset(source,profile=profile) as ds:
        pool=recommend(ds,params={ident:params})
        row=next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id']==ident)
        assert row['status']=='runnable'
        run(ds,algorithm_ids=[ident],params={ident:params},output=tmp_path/'results')
        with next(ds.iter_episodes()).opened() as ep:
            assert ep.channels['/fast'].clock=='arm_clock'
            np.testing.assert_allclose(ep.channels['/fast'].timestamps,np.arange(12)*.004+10)
    row=json.loads(next((tmp_path/'results/_runs').glob(f'*/{ident}/records.jsonl')).read_text())
    assert row['status']=='completed',row['reason']
    assert len(seen)==1
    np.testing.assert_allclose(seen[0]['/fast'],(np.arange(12)*.004+10)*1.1+.25)
    np.testing.assert_allclose(seen[0]['/head'],np.arange(3)*.032+10)
    assert len(seen[0]['/fast'])==12 and len(seen[0]['/head'])==3
    assert row['clock_normalization']
    fast=next(r for r in row['clock_normalization'] if r['channel']=='/fast')
    assert fast['params']['mapping']=='arm_to_head' and not fast['resampled']
    assert fast['time_source']=='acquisition'
    assert source.read_bytes()==before


@pytest.mark.parametrize('change',['missing_mapping','unknown_clock','range','synthetic'])
def test_target_name_alone_cannot_prove_sync(tmp_path,change):
    source,profile=mcap_fixture(tmp_path)
    if change=='missing_mapping':profile['transforms']={}
    if change=='unknown_clock':profile['channels']['/fast']['clock']=None
    if change=='range':profile['transforms']['clocks']['arm_to_head']['valid_range']=[0,1]
    if change=='synthetic':profile['channels']['/fast']['time_source']='synthetic'
    with open_dataset(source,profile=profile) as ds:
        pool=recommend(ds,params={'Sync_Check':{'channels':['rgb/head','/fast'],'target_clock':'head_clock'}})
        row=next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id']=='Sync_Check')
    assert row['status']=='missing_input'


def test_external_plugin_resolves_custom_channel_param_to_si(tmp_path,monkeypatch):
    import h5py
    from edc.algorithms.registry import AlgorithmSpec, algorithms
    class Probe:
        spec=AlgorithmSpec('test__si_probe','1','probe','test','custom param binding',
            parameter_schema={'type':'object','additionalProperties':False,
                'properties':{'signal':{'type':'string'}},'required':['signal']},
            data_requirements=({'id':'signal','channel_param':'signal','field':'position',
                'unit':'rad','source_kind':'feedback'},))
        def run(self,context,params):
            assert params['signal']=='actual'
            values=context.episode.read_signal(params['signal'],'position')
            return {'verdict':'OBSERVATION','values':values.tolist()}
    registry=algorithms();registry.register(Probe())
    monkeypatch.setattr('edc.execution.runner.algorithms',lambda:registry)
    monkeypatch.setattr('edc.algorithms.recommendations.algorithms',lambda:registry)
    source=tmp_path/'data.h5'
    with h5py.File(source,'w') as f:f['actual']=np.array([[0.],[90.]],dtype=np.float64)
    profile={'schema_version':1,'id':'si','version':'1','channels':{'actual':{
        'logical_name':'joint/arm/left/feedback','fields':['position'],'modality':'joint_space',
        'source_kind':'feedback','unit':'deg','representation':{'quantity':'joint_angle','semantic_status':'confirmed'}}}}
    with open_dataset(source,profile=profile) as ds:
        params={'test__si_probe':{'signal':'joint/arm/left/feedback'}}
        pool=recommend(ds,params=params)
        row=next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id']=='test__si_probe')
        assert row['status']=='runnable'
        run(ds,algorithm_ids=['test__si_probe'],params=params,output=tmp_path/'results')
    row=json.loads(next((tmp_path/'results/_runs').glob('*/test__si_probe/records.jsonl')).read_text())
    assert row['status']=='completed',row['reason']
    assert row['effective_params']['signal']=='actual'
    assert row['channel_bindings'][0]['requested']=='joint/arm/left/feedback'
    np.testing.assert_allclose(row['result']['reports'][0]['values'],[[0],[np.pi/2]])
    profile['channels']['actual']['representation']['semantic_status']='unknown'
    with open_dataset(source,profile=profile) as ds:
        pool=recommend(ds,params=params)
        row=next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id']=='test__si_probe')
    assert row['status']=='missing_input'


def test_selected_field_view_consumes_its_source_clock_mapping(tmp_path,monkeypatch):
    source,profile=mcap_fixture(tmp_path)
    profile['channels']['arm_view']={'modality':'joint_space','fields':['position'],
        'select':{'channel':'/fast','field':'position','indices':[0]}}
    from edc.algorithms.quality.algorithms.Sync_Check import run_check
    original=run_check.assess;seen=[]
    def capture(axes,**params):
        seen.append(axes)
        return original(axes,**params)
    monkeypatch.setattr(run_check,'assess',capture)
    with open_dataset(source,profile=profile) as ds:
        run(ds,algorithm_ids=['Sync_Check'],params={'Sync_Check':{
            'channels':['rgb/head','arm_view'],'target_clock':'head_clock'}},output=tmp_path/'results')
    row=json.loads(next((tmp_path/'results/_runs').glob('*/Sync_Check/records.jsonl')).read_text())
    assert row['status']=='completed',row['reason']
    np.testing.assert_allclose(seen[0]['arm_view'],(np.arange(12)*.004+10)*1.1+.25)
    mapping=next(v for v in row['clock_normalization'] if v['channel']=='arm_view')
    assert mapping['signal_selection']['channel']=='/fast'
