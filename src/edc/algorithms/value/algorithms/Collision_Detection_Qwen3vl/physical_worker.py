"""Native v62 physical pipeline over validated EDC arrays."""
import json
from pathlib import Path
import numpy as np
from edc.reporting.results import write_json
from .geometry_input import GeometryInput
from edc.execution.frames import PreparedFrames

class MappedEpisode(GeometryInput):
    def __init__(self,work,params):
        data=np.load(work/'physical-input.npz',allow_pickle=False)
        self.rgb=np.load(work/'rgb.npy',allow_pickle=False,mmap_mode='r')
        self.depth=np.load(work/'depth.npy',allow_pickle=False,mmap_mode='r')
        self.K,self.timestamps=data['K'],data['timestamps']
        self.frame_manifest=json.loads((work/'input-mapping.json').read_text())
        self.prepared=PreparedFrames(self.frame_manifest)
        self.n_frames=len(self.rgb);self.episode_dir=str(work/'input');self.video_path=str(work/'input/faceImg.mp4')
        self.task_desc=params['task'];self.depth_source=params['depth']['source']
        self.depth_confidence=params['depth'].get('confidence',0.5 if self.depth_source=='monocular_estimate' else .95)
        self.arms={a:{'t':self.timestamps,'ee':np.column_stack([data[f'{a}_position'],data[f'{a}_quaternion']]),
                      'gripper':data[f'{a}_gripper'][:,0]} for a in params['arms']}
        self.T_arm2cam={a:data[f'{a}_T'] for a in self.arms}
    def arm_state_at(self,index,arm):
        width_m=float(self.arms[arm]['gripper'][index])
        # v62 closure rules were tuned on cm-valued openings. Geometry is SI.
        return {'ee':self.arms[arm]['ee'][index], 'gripper':width_m*100,
                'gripper_width_m':width_m}
    def aux_frame(self, view, index):
        if view not in self.frame_manifest['views']:
            return None
        from PIL import Image
        with Image.open(self.prepared.image_path(view, index)) as image:
            return np.asarray(image.convert('RGB'))


def run_physical(work,out,model=None):
    from .pipeline import NeuroSymbolicPipeline,merge_collision_events
    from .grounding import SAM3Grounding,VLMGrounding
    from .tracker import CoTracker3D
    from .task_graph import TaskGraph
    from .appearance import AppearanceConfig
    from .run_four_event_pipeline import PHYSICAL_REVISION
    work=Path(work);p=json.loads((work/'request.json').read_text())['params']
    ep=MappedEpisode(work,p)
    grounding=VLMGrounding(p['models']['qwen'],p['device'],shared_model=model)
    grounding.evidence_dir=out/'grounding-evidence'
    pipe=NeuroSymbolicPipeline(str(work/'input'),out_dir=str(out),stride=p['collision_stride'],cam='faceImg',
        vlm=grounding,use_vlm_adjudicator=False,ep=ep,
        task_graph=TaskGraph.load(p['task_graph']),appearance_config=AppearanceConfig(model_name=p['models']['dinov2'],cache_dir=str(work/'model-cache')))
    pipe.phase1_ground(SAM3Grounding(p['models']['sam3']))
    entities=list(pipe.graph.entities.values())
    write_json(out/'grounding-summary.json',{'entities':[{'id':e.eid,'name':e.name,'category':e.category,
        'has_mask':e.mask is not None,'has_geometry':e.ellipsoid is not None or e.plane is not None} for e in entities]})
    np.savez_compressed(out/'grounding-masks.npz',**{e.eid:e.mask for e in entities if e.mask is not None})
    if not any(e.category=='A_target' and e.mask is not None and e.ellipsoid is not None for e in entities):
        raise RuntimeError('Physical detection incomplete: no grounded target geometry')
    pipe.phase2_track(CoTracker3D(p['models']['cotracker']))
    if not (out/'tracking-evidence.npz').is_file():
        raise RuntimeError('Physical detection incomplete: no tracking evidence')
    pipe.collision_segments=merge_collision_events(pipe.engine.events,task_graph=pipe.task_graph)
    report=pipe.report()
    payload={**report,'algorithm_revision':PHYSICAL_REVISION,
        'collision_candidates':report.get('collision_segments',[]),'events_raw':report.get('events',[]),
        'task_graph':report.get('task_graph'),'depth_source':ep.depth_source}
    out.mkdir(parents=True,exist_ok=True);write_json(out/'neuro_symbolic_report.json',payload)
    return out/'neuro_symbolic_report.json'
