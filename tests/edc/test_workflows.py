"""Cross-stage contracts: scope, immutable evidence, human gates and replay."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from edc import open_dataset, dataset_reference, open_dataset_reference, run_workflow, validate_workflow, review
from edc.execution.runner import Context
from edc.algorithms.registry import AlgorithmSpec, Registry, algorithms
from edc.workflows.gates import quality_gate, combine_gates
from edc.workflows.references import DatasetView
from edc.workflows.transforms import validate_transform


@pytest.fixture
def demo(tmp_path):
    pytest.importorskip('h5py')
    path = Path(__file__).resolve().parents[2] / 'examples/workflows/make_example.py'
    spec = importlib.util.spec_from_file_location('workflow_example', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.generate(tmp_path / 'demo')


def load(path):
    import yaml
    cfg = yaml.safe_load(path.read_text())
    cfg['dataset']['path'] = str(path.parent / 'source')
    return cfg


def record(verdict='ACCEPTABLE', status='completed', **fields):
    return {'algorithm': {'id': 'check', 'version': '1'}, 'object': {'type': 'episode', 'id': 'e'},
            'status': status, 'result': {'reports': [{'verdict': verdict, **fields}]}}


@pytest.mark.parametrize('row,decision', [
    (record(score=100), 'keep'), (record('DO_NOT_USE', score=100), 'exclude'),
    (record('NEEDS_REVIEW', score=100), 'review'),
    (record(assessment_complete=False), 'unassessed'),
    (record(status='missing_input'), 'unassessed'),
    (record(status='missing_dependency'), 'unassessed'),
    (record(status='processing_error'), 'unassessed'),
    (record(frame_quality={'review_candidates': [{'start': 1}]}), 'review'),
])
def test_quality_decision_independent_of_score(row, decision):
    assert quality_gate([row], ['e'], 'r')['e']['decision'] == decision


def test_gate_requires_every_selected_check_and_preserves_lineage():
    row = record()
    assert quality_gate([row], ['e'], 'r', required_algorithms=['check','other'])['e']['decision'] == 'unassessed'
    previous = quality_gate([record('DO_NOT_USE')], ['e'], 'prior')
    assert combine_gates(previous, quality_gate([row], ['e'], 'next'))['e']['decision'] == 'exclude'


def test_dataset_quality_result_applies_only_to_its_evaluated_subset():
    row=record()
    row['object']={'type':'dataset','id':'dataset'}
    row['eligible_episodes']=['e0']
    result=quality_gate([row],['e0','e1'],'run')
    assert result['e0']['decision']=='keep'
    assert result['e1']['decision']=='unassessed'


def test_review_exact_scope_cannot_fill_missing_coverage():
    from edc.workflows.reviews import subject_id
    row = record('NEEDS_REVIEW', score=100)
    decision = {'run_id':'r', 'subject_id':subject_id(row,0), 'action':'dismissed'}
    assert quality_gate([row], ['e'], 'r', [decision])['e']['decision'] == 'keep'
    assert quality_gate([row], ['e'], 'another-run', [decision])['e']['decision'] == 'review'
    decision['action']='confirmed'
    assert quality_gate([row], ['e'], 'r', [decision])['e']['decision'] == 'exclude'
    row['result']['reports'][0]['assessment_complete'] = False
    decision.update(action='dismissed', subject_id=subject_id(row,0))
    assert quality_gate([row], ['e'], 'r', [decision])['e']['decision'] == 'unassessed'
    assert row['result']['reports'][0]['score'] == 100


def test_context_upstream_is_deeply_readonly(tmp_path):
    original={'quality':{'records':[{'score':100}]}}
    ctx=Context(None,None,tmp_path,upstream=original)
    with pytest.raises(TypeError): ctx.upstream['quality']['records'][0]['score']=0
    original['quality']['records'][0]['score']=7
    assert ctx.upstream['quality']['records'][0]['score']==100


@pytest.mark.parametrize('change', ['cycle','category','value_first','augment_first','where','unknown_param','transform_select'])
def test_invalid_workflow_rejected_before_output(demo,tmp_path,change):
    cfg=load(demo)
    if change=='cycle': cfg['stages'][0]['input']='aug_recheck'
    if change=='category': cfg['stages'][0]['kind']='value'
    if change=='value_first': cfg['stages'][3]['input']='source'
    if change=='augment_first': cfg['stages'][4]['input']='quality'
    if change=='where': cfg['stages'][1]['select']['where']='invalid'
    if change=='unknown_param': cfg['stages'][0]['params']={'Structure_Check':{'bad':1}}
    if change=='transform_select': cfg['stages'][1].pop('select')
    target=tmp_path/'bad-output'
    with pytest.raises(ValueError): run_workflow(cfg,target)
    assert not target.exists()


def test_reference_roundtrip_preserves_subset_and_detects_stale_content(demo):
    cfg=load(demo)
    with open_dataset(cfg['dataset']['path']) as ds:
        ref=dataset_reference(DatasetView(ds,['episode_1']))
    with open_dataset_reference(ref) as restored:
        ep,=restored.iter_episodes()
        assert ep.id=='episode_1' and ep.selector=={'group':'/'}
        with ep.opened():
            a=ep.read_signal('joint_pos','position')
            assert a.dtype==np.float64
            assert a[-1,-1]==90
    bad=copy.deepcopy(ref);bad['episodes'][0]['selector']={'group':'/wrong'}
    with pytest.raises(ValueError,match='modified'):open_dataset_reference(bad)
    source=Path(ref['episodes'][0]['source'])
    with source.open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='stale'):open_dataset_reference(ref)


def test_full_chain_recheck_and_resume(demo,tmp_path):
    output=tmp_path/'results'
    before=dataset_reference(open_dataset(demo.parent/'source'))
    first=run_workflow(demo,output)
    assert first['status']=='completed'
    assert [len(s['selected_episodes']) for s in first['stages']]==[2,2,2,2,2,4]
    value=first['stages'][3]
    records=list((output/'_runs'/value['run_id']).glob('*/records.jsonl'))
    rows=[json.loads(line) for f in records for line in f.read_text().splitlines()]
    assert len(rows)==1 and rows[0]['object']['type']=='dataset'
    assert rows[0]['result']['reports'][0]['episode_count']==2
    assert dataset_reference(open_dataset(demo.parent/'source'))==before
    with open_dataset_reference(first['stages'][-1]['output_reference']) as derived:
        assert len(list(derived.iter_episodes()))==4
        for ep in derived.iter_episodes():
            assert ep.metadata['curation_parent']['rows']=='identity'
            with ep.opened(): assert ep.read_signal('joint_pos','position').dtype==np.float64
    second=run_workflow(demo,output,resume=True)
    assert second['status']=='completed'
    assert all(s['reused'] for s in second['stages'])
    assert [s['run_id'] for s in first['stages']]==[s['run_id'] for s in second['stages']]
    assert (output/'report.html').exists()
    assert len(json.loads((output/'catalog.json').read_text())['workflows'])==2
    # Deleting an evidence file invalidates reuse, never quietly trusts the summary.
    first_artifact=next((output/'_runs'/first['stages'][0]['run_id']).rglob('unit-normalization.json'))
    first_artifact.unlink()
    third=run_workflow(demo,output,resume=True)
    assert not third['stages'][0]['reused']
    assert third['status']=='completed'
    cfg=load(demo);cfg['stages'][4]['params']['team_example__image_augment']['variants']=1
    fourth=run_workflow(cfg,output,resume=True)
    assert all(s['reused'] for s in fourth['stages'][:4])
    assert not fourth['stages'][4]['reused']
    assert len(fourth['stages'][-1]['selected_episodes'])==2


def test_transforms_reject_escaping_or_false_provenance(demo,tmp_path):
    first=run_workflow(demo,tmp_path/'results')
    stage=first['stages'][1]
    root=tmp_path/'results'
    row=json.loads(next((root/'_runs'/stage['run_id']).glob('*/records.jsonl')).read_text().splitlines()[0])
    row['_run_id']=stage['run_id']
    with open_dataset_reference(first['stages'][0]['output_reference']) as parents:
        bad=copy.deepcopy(row);bad['result']['reports'][0]['output']=str(demo.parent/'source')
        with pytest.raises(ValueError,match='artifacts'):validate_transform(bad,parents,root)
        bad=copy.deepcopy(row);bad['result']['reports'][0]['source_mapping']['parent_fingerprint']='wrong'
        with pytest.raises(ValueError,match='fingerprint'):validate_transform(bad,parents,root)
        bad=copy.deepcopy(row);bad['result']['reports'][0]['source_mapping']['episodes'][0]['rows']='resampled'
        with pytest.raises(ValueError,match='identity'):validate_transform(bad,parents,root)
        # Undeclared numeric damage must be caught even when files can be opened.
        target=row['result']['reports'][0]['output']
        import h5py
        with h5py.File(next(Path(target).glob('*.h5')),'a') as f:f['joint_pos'][0,0]=1234
        with pytest.raises(ValueError,match='undeclared numeric'):validate_transform(row,parents,root)


class CandidateQuality:
    spec=AlgorithmSpec('test__candidate','1','candidate','test','human boundary')
    def run(self,context,params):return {'verdict':'ACCEPTABLE','score':100,'review_candidates':[{'frame':2}]}


class ValueCapture:
    calls=[]
    spec=AlgorithmSpec('test__value','1','value','test','dataset boundary',category='value',granularity='dataset')
    def preflight_dataset(self,dataset,params):return 'runnable',''
    def run(self,context,params):
        self.calls.append([e.id for e in context.dataset.iter_episodes()])
        assert context.upstream
        return {'distribution':{'demo':len(self.calls[-1])}}


@pytest.fixture
def test_registry(monkeypatch):
    registry=algorithms();registry.register(CandidateQuality());capture=ValueCapture();capture.calls=[];registry.register(capture)
    for module in ('edc.workflows.engine','edc.workflows.validation','edc.execution.runner'):
        monkeypatch.setattr(module+'.algorithms',lambda:registry)
    return capture


def test_review_resume_and_explicit_subset(demo,tmp_path,test_registry):
    cfg=load(demo);cfg['stages']=[
        {'id':'q','kind':'quality','input':'source','algorithms':['test__candidate']},
        {'id':'v','kind':'value','input':'q','algorithms':['test__value'],'select':{'episodes':['episode_0']}}]
    output=tmp_path/'results'
    first=run_workflow(cfg,output)
    assert first['stages'][1]['status']=='pending_review' and not test_registry.calls
    original_records=next((output/'_runs'/first['stages'][0]['run_id']).glob('*/records.jsonl')).read_bytes()
    review(output,run_id=first['stages'][0]['run_id'],algorithm='test__candidate',object_id='episode_0',
        action='dismissed',reviewer='test reviewer',reason='static scene confirmed',evidence='frame 2')
    second=run_workflow(cfg,output,resume=True)
    assert second['stages'][0]['reused']
    assert second['stages'][0]['gate']['episode_1']['decision']=='review'
    assert second['stages'][1]['selected_episodes']==['episode_0']
    assert test_registry.calls==[['episode_0']]
    assert next((output/'_runs'/first['stages'][0]['run_id']).glob('*/records.jsonl')).read_bytes()==original_records
    review(output,run_id=first['stages'][0]['run_id'],algorithm='test__candidate',object_id='episode_0',
        action='confirmed',reviewer='test reviewer',reason='confirmed violation',evidence='frame 2')
    third=run_workflow(cfg,output,resume=True)
    assert third['stages'][1]['status']=='blocked'
    assert third['stages'][1]['requested_episodes']==['episode_0']
    assert third['stages'][1]['withheld_episodes'][0]['quality']['decision']=='exclude'
    assert test_registry.calls==[['episode_0']]


def test_review_cannot_make_failed_execution_pass(demo,tmp_path):
    cfg=load(demo);cfg['stages']=cfg['stages'][:1]
    r=run_workflow(cfg,tmp_path/'results')
    with pytest.raises(ValueError,match='no review candidate'):
        review(tmp_path/'results',run_id=r['stages'][0]['run_id'],algorithm='Structure_Check',object_id='episode_0',
            action='dismissed',reviewer='tester',reason='attempt',evidence='none')


def test_algorithm_version_change_invalidates_reuse(demo,tmp_path,test_registry):
    from dataclasses import replace
    cfg=load(demo);cfg['stages']=[{'id':'q','kind':'quality','input':'source','algorithms':['test__candidate']}]
    output=tmp_path/'results'
    first=run_workflow(cfg,output)
    registry=__import__('edc.workflows.engine',fromlist=['algorithms']).algorithms()
    plugin=registry.plugins['test__candidate']
    old_spec=plugin.spec
    try:
        plugin.spec=replace(old_spec,version='2')
        second=run_workflow(cfg,output,resume=True)
        assert not second['stages'][0]['reused']
        assert first['stages'][0]['run_id']!=second['stages'][0]['run_id']
    finally:plugin.spec=old_spec


def test_value_failure_blocks_augmentation(demo,tmp_path,test_registry,monkeypatch):
    def fail(*args):raise RuntimeError('value unavailable')
    monkeypatch.setattr(test_registry,'run',fail)
    cfg=load(demo)
    cfg['stages']=[cfg['stages'][0],
        {'id':'value','kind':'value','input':'quality','algorithms':['test__value']},cfg['stages'][4]]
    r=run_workflow(cfg,tmp_path/'results')
    assert r['stages'][1]['processing']=={'processing_error':1}
    assert r['stages'][1]['status']=='processing_error'
    assert r['stages'][1]['value_completed_episodes']==[]
    assert r['stages'][2]['status']=='blocked'
    assert r['stages'][2]['selected_episodes']==[]


def test_sidecar_change_invalidates_data_reference(demo):
    with open_dataset(demo.parent/'source') as ds:
        ep=next(ds.iter_episodes())
        sidecar=Path(str(ep.path)+'.frames.json');sidecar.write_text('{"start_frame":0}')
        reference=dataset_reference(ds)
        sidecar.write_text('{"start_frame":1}')
    with pytest.raises(ValueError,match='stale'):open_dataset_reference(reference)


def test_partial_transform_only_publishes_valid_children(demo,tmp_path,monkeypatch):
    registry=algorithms();plugin=registry.plugins['team_example__image_edit'];original=plugin.run
    def partial(context,params):
        if context.episode.id=='episode_1':raise ValueError('deliberate episode failure')
        return original(context,params)
    monkeypatch.setattr(plugin,'run',partial)
    for module in ('edc.workflows.engine','edc.workflows.validation','edc.execution.runner'):
        monkeypatch.setattr(module+'.algorithms',lambda:registry)
    cfg=load(demo);cfg['stages'][3]['params']={'team_example__dataset_inventory':{'min_episodes':1}}
    r=run_workflow(cfg,tmp_path/'results')
    assert r['stages'][1]['status']=='partial_success'
    assert len(r['stages'][1]['transform_errors'])==1
    assert len(r['stages'][2]['selected_episodes'])==1
    assert len(r['stages'][3]['selected_episodes'])==1
    assert len(r['stages'][-1]['selected_episodes'])==2


def test_workflow_cli_roundtrip(demo,tmp_path,capsys):
    from edc.execution.cli import main
    assert main(['workflow','check',str(demo)])==0
    assert json.loads(capsys.readouterr().out)['valid']
    assert main(['workflow','run',str(demo),'--output',str(tmp_path/'results')])==0
    output=json.loads(capsys.readouterr().out)
    assert output['status']=='completed'
    assert len(output['stages'][-1]['selected_episodes'])==4
