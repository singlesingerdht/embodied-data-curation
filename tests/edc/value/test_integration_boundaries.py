"""Contract failures must preserve evidence without manufacturing good data."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pytest
from edc.algorithms.value import runtime
from edc.algorithms.value.algorithms.Initial_State_Distribution import spatial
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.segmentation import validate_intervals


def episode(times=(10.,10.1,10.2),clock='camera'):
    c=NS(modality='rgb',image_available=True,count=len(times),clock=clock,time_source='acquisition',
         timestamps=list(times),source_mapping={'source_indices':[0,1,2]})
    return NS(channels={'rgb':c})


def test_uniform_source_time_and_mapping_retained():
    ep=episode();row=runtime.video_inputs(ep,{'main':'rgb'})['main']
    assert row['source_timestamps']==[10.,10.1,10.2]
    assert row['fps']==pytest.approx(10)
    assert row['reference_clock']=='camera'


@pytest.mark.parametrize('change',[{'clock':None},{'time_source':'synthetic'},{'timestamps':[0,1,3]},
    {'timestamps':[0,0,1]},{'image_available':False}])
def test_invalid_video_timeline_rejected(change):
    ep=episode();vars(ep.channels['rgb']).update(change)
    with pytest.raises(ValueError):runtime.video_inputs(ep,{'main':'rgb'})


def test_different_clock_needs_explicit_mapping():
    ep=episode();ep.channels['aux']=episode((20.,20.1,20.2),'aux_clock').channels['rgb']
    def unmapped(*args):raise ValueError('no clock mapping')
    ep.transform_timestamps=unmapped
    with pytest.raises(ValueError,match='clock mapping'):runtime.video_inputs(ep,{'main':'rgb','aux':'aux'})
    ep.transform_timestamps=lambda name,clock:NS(output=np.array([10.,10.1,10.2]))
    rows=runtime.video_inputs(ep,{'main':'rgb','aux':'aux'})
    assert rows['aux']['clock']=='aux_clock'
    assert rows['aux']['mapped_timestamps']==rows['main']['mapped_timestamps']


def test_provenance_failure_prevents_worker_and_source_hashes_are_present(tmp_path,monkeypatch):
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import algorithm
    model=tmp_path/'model';model.mkdir();(model/'weights').write_bytes(b'weights')
    monkeypatch.setattr(runtime,'runtime_info',lambda *a:{'package':str(Path(algorithm.__file__).with_name('__init__.py')),'versions':{'torch':'test'},
        'source_files':{p.relative_to(Path(algorithm.__file__).parent).as_posix():runtime.sha256(p) for p in Path(algorithm.__file__).parent.rglob('*') if p.is_file() and p.suffix in {'.py','.json','.yaml'}},
        'shared_runtime_sha256':runtime.sha256(runtime.__file__)})
    ctx=NS(workdir=tmp_path)
    saved=runtime.save_provenance(ctx,{},algorithm.__file__,{'model':model},[])
    assert saved==json.loads((tmp_path/'provenance.json').read_text())
    assert saved['models']['model']['files']['weights']==runtime.sha256(model/'weights')
    assert 'algorithm.py' in saved['source_files'] and saved['shared_runtime_sha256']
    monkeypatch.setattr(runtime,'write_json',lambda *a:(_ for _ in ()).throw(OSError('no space')))
    with pytest.raises(OSError):runtime.save_provenance(ctx,{},algorithm.__file__,{'model':model},[])


def test_worker_failure_keeps_request_log_and_provenance(tmp_path,monkeypatch):
    (tmp_path/'provenance.json').write_text('{}')
    def failed(*a,**kw):kw['stdout'].write('model initialization failed');return NS(returncode=17)
    monkeypatch.setattr(runtime.subprocess,'run',failed)
    with pytest.raises(RuntimeError,match='17'):runtime.run_worker(NS(workdir=tmp_path),{},'example',{})
    assert (tmp_path/'request.json').is_file()
    assert 'initialization' in (tmp_path/'worker.log').read_text()
    assert (tmp_path/'provenance.json').read_text()=='{}'


def spatial_fixture(tmp_path,unit='m'):
    p=tmp_path/f'{unit}.npy';depth=np.ones((50,60),float);depth[20:30,20:30]=.9
    np.save(p,depth*(1000 if unit=='mm' else 1))
    mask=np.zeros((50,60),bool);mask[20:30,20:30]=True
    cfg={'source':'sensor_depth','unit':unit,'registered_to_rgb':True,'registration_id':'calibration-test',
         'intrinsics':{'fx':50.,'fy':50.,'cx':30.,'cy':25.},
         'plane':{'mask_dilation_px':3,'ransac_iterations':30},
         'episodes':{'a':{'path':str(p),'rgb_frame_index':0,'depth_source_index':0,'correspondence_evidence':'test'}}}
    row={'width':60,'height':50,'categories':{'cup':{'instances':[{'_mask':mask}]}}}
    return cfg,row


def test_spatial_units_preserve_geometry_and_are_separate_from_2d(tmp_path):
    cfg,row=spatial_fixture(tmp_path);a=spatial.describe_scene(row,cfg,'a',tmp_path,'a')
    cfg,row=spatial_fixture(tmp_path,'mm');b=spatial.describe_scene(row,cfg,'a',tmp_path,'b')
    assert a['objects']==b['objects']
    assert a['objects'][0]['height_above_support_m']==pytest.approx(.1)
    assert a['coordinate_frame']=='camera' and a['contributes_to_2d_score'] is False
    assert a['support_plane']['inlier_ratio']>.9


@pytest.mark.parametrize('change',[{'unit':'unknown'},{'registered_to_rgb':False},{'registration_id':''},
    {'source':'guessed'},{'source':'monocular_estimate','model':'/missing/model'}])
def test_spatial_requires_explicit_calibration(tmp_path,change):
    cfg,_=spatial_fixture(tmp_path);cfg.update(change)
    with pytest.raises(ValueError):spatial.validate_inputs(NS(iter_episodes=lambda:iter([NS(id='a')])),{'spatial':cfg,'frame_index':0})


def test_spatial_plane_and_resolution_failure_are_errors(tmp_path):
    cfg,row=spatial_fixture(tmp_path)
    np.save(cfg['episodes']['a']['path'],np.zeros((50,60)))
    with pytest.raises(ValueError,match='候选点不足'):spatial.describe_scene(row,cfg,'a',tmp_path,'a')
    np.save(cfg['episodes']['a']['path'],np.ones((10,10)))
    with pytest.raises(ValueError,match='dimensions'):spatial.describe_scene(row,cfg,'a',tmp_path,'a')
