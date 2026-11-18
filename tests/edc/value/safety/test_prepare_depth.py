"""Depth preparation preserves raw data and model-failure evidence."""
from contextlib import nullcontext
import hashlib
import json
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import prepare_depth


def fixture(tmp_path, monkeypatch, failure=False):
    raw=tmp_path/'raw';raw.mkdir()
    model=tmp_path/'model';model.mkdir();(model/'weights').write_bytes(b'synthetic')
    frames=[np.full((8,8,3),i,dtype=np.uint8) for i in range(3)]
    ep=NS(path=raw,id='fixture',selector={'episode_index':2},channels={
        'rgb':NS(count=3,modality='rgb')},opened=lambda:nullcontext())
    ep.iter_frames=lambda channel:iter({'index':i,'source_index':i+10,'image':im} for i,im in enumerate(frames))
    ds=NS(iter_episodes=lambda:iter([ep]))
    monkeypatch.setattr(prepare_depth,'open_dataset',lambda *a,**kw:nullcontext(ds))
    calls=[]
    class Model:
        @classmethod
        def from_pretrained(cls,path):
            calls.append('initialize')
            if failure:raise RuntimeError('synthetic init failure')
            return cls()
        def to(self,device):return self
        def eval(self):return self
        def inference(self,image,**kw):
            calls.append(int(image[0][0,0,0]))
            return NS(depth=np.ones((1,4,4),dtype=np.float32)*2,
                      intrinsics=np.eye(3)[None])
    monkeypatch.setitem(sys.modules,'depth_anything_3.api',NS(DepthAnything3=Model))
    return raw,model,frames,calls


def test_preparation_keeps_selected_original_rgb_and_scale(tmp_path,monkeypatch):
    raw,model,frames,calls=fixture(tmp_path,monkeypatch)
    output=tmp_path/'derived'
    record=prepare_depth.prepare(raw,{},model,output,channel='rgb',frame_range=[1,3],depth_scale=.5)
    assert calls==['initialize',1,2]
    assert record['status']=='completed' and record['source']=='monocular_estimate'
    assert record['frame_range']==[1,3]
    assert [r['source_index'] for r in record['frames']]==[11,12]
    assert [r['rgb_sha256'] for r in record['frames']]==[hashlib.sha256(im.tobytes()).hexdigest() for im in frames[1:]]
    assert np.all(np.load(output/'depth.npy')==1)
    assert not list(raw.iterdir())


def test_manifest_cannot_write_depth_inside_referenced_raw_episode(tmp_path,monkeypatch):
    raw,model,frames,calls=fixture(tmp_path,monkeypatch)
    manifest=tmp_path/'dataset.json';manifest.write_text('{}')
    output=raw/'derived'
    with pytest.raises(ValueError,match='inside the selected raw episode'):
        prepare_depth.prepare(manifest,{},model,output,channel='rgb')
    assert not output.exists() and not calls


def test_init_failure_retains_fingerprints_before_any_inference(tmp_path,monkeypatch):
    raw,model,frames,calls=fixture(tmp_path,monkeypatch,failure=True)
    output=tmp_path/'derived'
    with pytest.raises(RuntimeError,match='synthetic init failure'):
        prepare_depth.prepare(raw,{},model,output,channel='rgb')
    record=json.loads((output/'provenance.json').read_text())
    assert record['status']=='preparing' and record['model']
    assert record['episode_id']=='fixture'
    assert not (output/'depth.npy').exists()
