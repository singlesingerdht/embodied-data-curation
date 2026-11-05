import json
import numpy as np
import pytest
import yaml
from edc.data.ir.dataset import open_dataset
from edc.robots.profiles import load_profile
from edc.data.ir.modalities import MODALITIES
from edc.execution.runner import run


def test_modality_vocabulary_separates_anatomy_and_commands(tmp_path):
    root=tmp_path/'ep';root.mkdir()
    (root/'trajectory.json').write_text(json.dumps({'data':[{'q':{'position':[1,2]},'touch':{'pressure':[[1,2],[3,4]]}}]}))
    profile={'schema_version':1,'id':'robot','version':'1','channels':{
        'q':{'modality':'joint_space','fields':['position'],'side':'left','body_part':'hand','source_kind':'command'},
        'touch':{'modality':'tactile','fields':['pressure'],'side':'left','body_part':'hand','sensor_id':'fingertip',
                 'representation':{'layout':'taxel_grid','shape':[2,2]}}}}
    with next(open_dataset(root,profile=profile).iter_episodes()).opened() as ep:
        assert ep.channels['q'].modality=='joint_space'
        assert ep.raw('q').modality=='cmd'
        assert ep.channels['touch'].representation['layout']=='taxel_grid'
        assert ep.read_signal('touch','pressure').shape==(1,2,2)
    for name in MODALITIES:
        load_profile({**profile,'channels':{'x':{'modality':name}}})
    load_profile({**profile,'channels':{'x':{'modality':'lab__new_sensor'}}})
    with pytest.raises(ValueError):load_profile({**profile,'channels':{'x':{'modality':'left_hand'}}})


def test_depth_tensor_not_rgb(tmp_path):
    h5py=pytest.importorskip('h5py');path=tmp_path/'ep.h5'
    with h5py.File(path,'w') as f:f['depth']=np.ones((2,3,4),dtype=np.uint16)*1000
    p={'schema_version':1,'id':'robot','version':'1','channels':{'depth':{'modality':'depth','fields':['distance'],'unit':'mm','dimensions':[3,4], 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
    with next(open_dataset(path,profile=p).iter_episodes()).opened() as ep:
        assert ep.channels['depth'].modality=='depth' and not ep.channels['depth'].image_available
        assert ep.read_signal('depth','distance').shape==(2,3,4)


def manifest(root):
    (root/'ep').mkdir()
    (root/'ep/trajectory.json').write_text(json.dumps({'data':[{'joint_pos':[0]}]}))
    return {'schema_version':1,'id':'demo','tasks':[{'id':'pour','name':'倒茶','description':'向杯中倒茶',
        'subtasks':[{'id':'grasp','name':'抓壶','description':'拿起茶壶'}]}],
        'metadata':{'collection_site':'lab','custom':{'operator_code':'anon'}},
        'episodes':[{'id':'e1','path':'ep','metadata':{'task_ids':['pour'],
        'subtask_annotations':[{'task_id':'pour','subtask_id':'grasp','source':'human',
            'span':{'unit':'frame','channel':'joint_pos','start':0,'end':1}}]}}]}


def test_task_catalog_and_annotations_persist_into_run(tmp_path):
    data=manifest(tmp_path);(tmp_path/'dataset.yaml').write_text(yaml.safe_dump(data,allow_unicode=True))
    with open_dataset(tmp_path) as ds:
        assert ds.task_catalog['pour']['subtasks'][0]['name']=='抓壶'
        assert ds.describe()['episodes'][0]['metadata']['task_ids']==['pour']
        result=run(ds,output=tmp_path/'output',algorithm_ids=['Structure_Check'])
    saved=json.loads((tmp_path/'output/_runs'/result['run_id']/'run.json').read_text())['dataset']
    assert saved['tasks']==data['tasks'] and saved['metadata']==data['metadata']
    assert saved['episodes'][0]['metadata']==data['episodes'][0]['metadata']


@pytest.mark.parametrize('change',['task','subtask','span','clock'])
def test_annotation_invalid_references_rejected(tmp_path,change):
    data=manifest(tmp_path);meta=data['episodes'][0]['metadata'];a=meta['subtask_annotations'][0]
    if change=='task':meta['task_ids']=['missing']
    if change=='subtask':a['subtask_id']='missing'
    if change=='span':a['span']['end']=0
    if change=='clock':a['span']={'unit':'s','start':0,'end':1}
    (tmp_path/'dataset.yaml').write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):open_dataset(tmp_path)
