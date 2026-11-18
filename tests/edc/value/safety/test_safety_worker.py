import contextlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import numpy as np
import pytest
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import grounding, worker


def test_grounding_receives_image_and_keeps_raw_evidence(tmp_path):
    frame=np.full((8,12,3),127,dtype=np.uint8)
    class Inputs(dict):
        input_ids=np.ones((1,2),int)
        def to(self,device):return self
    class Processor:
        def apply_chat_template(self,*a,**kw):return 'image and text'
        def __call__(self,**kw):
            assert np.array_equal(kw['images'][0],frame)
            assert kw['text']==['image and text']
            return Inputs(pixel_values=frame)
        def batch_decode(self,*a,**kw):
            return ['[{"name":"table","category":"B_env","box_xyxy":[0,0,12,8]}]']
    g=object.__new__(grounding.VLMGrounding)
    g.processor=Processor();g.model=NS(device='cpu',generate=lambda **kw:np.ones((1,5),int))
    g.torch=NS(inference_mode=contextlib.nullcontext);g.evidence_dir=tmp_path
    result=g.detect(frame,'pick')
    assert result[0]['category']=='B_env'
    assert (tmp_path/'frame.png').is_file()
    assert 'table' in (tmp_path/'response.txt').read_text()


@pytest.mark.parametrize('incomplete',[False,True])
@pytest.mark.parametrize('verdict,expected', [('confirmed','FAIL'),('needs_review','NEEDS_REVIEW'),('rejected','PASS')])
def test_worker_keeps_all_four_checks_and_incomplete_evidence(tmp_path,monkeypatch,incomplete,verdict,expected):
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import run_four_event_pipeline as native
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import safety_framework
    fake_torch=NS(cuda=NS(set_device=lambda device:None),device=lambda value:value)
    monkeypatch.setitem(sys.modules,'torch',fake_torch)
    monkeypatch.setattr(safety_framework,'LocalQwenVisionModel',lambda *a:NS())
    def main(args,model):
        out=Path(args[args.index('--out')+1]);out.mkdir()
        report={'safety_capabilities':{key:{'enabled':True} for key in worker.EVENTS},
                'scan_coverage':{'direct_scan_complete':not incomplete}}
        (out/'safety_report.json').write_text(json.dumps(report))
        (out/'events_adjudicated.json').write_text(json.dumps([{'verdict':verdict}]))
        return 0
    monkeypatch.setattr(native,'main',main)
    request={'workdir':str(tmp_path),'params':{'device':'cuda:0','models':{'qwen':'local-model'},
        'task':'test','task_graph':'explicit.json','collision_stride':2}}
    if incomplete:
        with pytest.raises(RuntimeError,match='incomplete'):worker.execute(request)
    else:
        assert worker.execute(request)['assessment_complete']
    result=json.loads((tmp_path/'native-result.json').read_text())
    assert set(result['checks'])==set(worker.EVENTS)
    assert result['assessment_complete']==(not incomplete)
    if incomplete:
        assert 'verdict' not in result
    else:
        assert result['verdict']==expected
        assert result['event_counts'][verdict]==1
    assert 'score' not in result


def test_worker_model_failure_keeps_previously_saved_evidence(tmp_path,monkeypatch):
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import safety_framework
    monkeypatch.setitem(sys.modules,'torch',NS(cuda=NS(set_device=lambda x:None),device=lambda x:x))
    (tmp_path/'provenance.json').write_text('{"source":"frozen"}')
    def fail(*a):raise RuntimeError('model init failure')
    monkeypatch.setattr(safety_framework,'LocalQwenVisionModel',fail)
    with pytest.raises(RuntimeError,match='model init'):
        worker.execute({'workdir':str(tmp_path),'params':{'device':'cuda:0','models':{'qwen':'missing'}}})
    assert json.loads((tmp_path/'provenance.json').read_text())=={'source':'frozen'}
