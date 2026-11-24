"""Four-event v62 adapter, with explicit view and physical mappings."""
from pathlib import Path
import sys
import numpy as np
from edc.algorithms.registry import AlgorithmSpec
from edc.algorithms.channel_names import resolve_channel
from edc.algorithms.value.runtime import save_provenance,run_worker
from edc.execution.frames import inspect_frames,cache_frames,render_videos
from edc.data.transforms.core import convert_units
from .input_contract import ARM, DEPTH, selected_frames, validate_configuration, input_bindings, check_device

EVENTS=('collision','spill','hand_intervention','lid_fall')

def inspect_safety_frames(episode, params, upstream=None):
    """Use the same original-frame contract as progress attribution.

    Physical safety needs recorded time; encoding rate is not a quality gate.
    Missing view coverage is never silently padded or reported as no event.
    """
    manifest = inspect_frames(episode, params['views'], reference='faceImg', upstream=upstream)
    ref = manifest['views']['faceImg']
    selected=selected_frames(params,manifest['frame_count'])
    start,end=selected[0],selected[-1]+1
    manifest['source_frame_count']=manifest['frame_count']
    manifest['selected_reference_indices']=list(range(start,end))
    manifest['frame_count']=end-start
    for row in manifest['views'].values():
        row['reference_indices']=row['reference_indices'][start:end]
        row['pairing_offsets_sec']=row['pairing_offsets_sec'][start:end]
        row['missing_reference_indices']=[i for i,j in enumerate(row['reference_indices']) if j is None]
    manifest['coverage_complete']=all(not row['missing_reference_indices'] for row in manifest['views'].values())
    if ref['source_timestamps'] is None or not ref['clock']:
        raise ValueError('Physical safety requires recorded source time and clock; playback alone is insufficient')
    missing = {role: len(row['missing_reference_indices']) for role, row in manifest['views'].items()
               if row['missing_reference_indices']}
    if missing:
        raise ValueError(f'Incomplete multi-view pairing coverage: {missing}')
    return manifest


def physical_arrays(episode,params,reference,evidence=None,reference_indices=None):
    data={};ref=episode.channels[reference]
    count=len(reference_indices) if reference_indices is not None else ref.count
    for arm,bindings in params['arms'].items():
        if arm not in {'left','right'}: raise ValueError('Arm keys must be left/right')
        if 'v62_gripper_calibration' in bindings:
            raise ValueError('Use robot field calibration_status/calibration_reference; remove v62_gripper_calibration')
        for role,unit,size in (('position','m',3),('quaternion','1',4),('gripper','m',1),('terminal_effort','N*m',1)):
            spec=bindings[role]
            extra=set(spec)-{'channel','field','indices','max_gap_sec','order'}
            if extra:raise ValueError(f'{role}: unsupported binding fields {sorted(extra)}; physical metadata belongs in the robot profile')
            name=resolve_channel(episode,spec['channel']);c=episode.channels[name]
            field=spec['field'];meta=c.field_spec(field)
            if meta['source_kind']!='feedback' or meta['calibration_status']!='confirmed':
                raise ValueError('Physical inputs require source_kind=feedback and calibration_status=confirmed in the robot profile')
            if c.time_source not in {'acquisition','container'} or not c.clock:
                raise ValueError('Physical timestamps and clock required')
            order=spec.get('order')
            normalized=role=='quaternion' and meta.get('normalization_status') in {'conversion_required','validation_required'}
            if role=='quaternion' and normalized:
                rep=meta.get('representation',{})
                if rep.get('kind')!='quaternion' or rep.get('order')!='xyzw':
                    raise ValueError('Unsupported normalized orientation representation')
                order='xyzw'  # EDC already converted source orientations; never reorder twice.
            if role=='quaternion' and order not in {'wxyz','xyzw','rotvec'}:
                raise ValueError('Explicit orientation order wxyz, xyzw or rotvec required')
            # Quaternion components must not be interpolated as scalar channels.
            method = 'nearest' if role == 'quaternion' else 'linear'
            result=episode.align_signal(name,field,reference,method=method,max_gap=spec.get('max_gap_sec'),
                **({'reference_indices':reference_indices} if reference_indices is not None else {}))
            source_unit=meta['unit']
            if role=='quaternion':
                units = {'rad'} if order=='rotvec' else {'1','quaternion'}
                if source_unit not in units: raise ValueError('Orientation unit does not match its representation')
                converted=result.output
            else:
                converted=convert_units(result.output,source_unit,unit).output
            values=np.asarray(converted,float)
            if values.ndim==1:values=values[:,None]
            if 'indices' in spec:
                indices=spec['indices']
                if (not isinstance(indices,list) or not indices or len(set(indices))!=len(indices) or
                        any(type(i) is not int or i<0 or i>=values.shape[1] for i in indices)):
                    raise ValueError(f'{arm}.{role}: indices must select unique existing components')
                if normalized and indices!=[0,1,2,3]:
                    raise ValueError('Normalized orientations require all XYZW components in order')
                values=values[:,indices]
            if role=='quaternion':
                if order=='rotvec':
                    from scipy.spatial.transform import Rotation
                    if values.shape!=(count,3):raise ValueError('Rotation vectors require three components')
                    values=Rotation.from_rotvec(values).as_quat()
                elif order=='wxyz':
                    if values.shape!=(count,4):raise ValueError('Quaternions require four components')
                    values=values[:,[1,2,3,0]]
            if values.shape!=(count,size) or not np.isfinite(values).all():raise ValueError(f'Invalid {arm} {role} values')
            if role=='quaternion' and not np.allclose(np.linalg.norm(values,axis=1),1,atol=1e-3):
                raise ValueError('Unit quaternions required')
            data[f'{arm}_{role}']=values
            if evidence is not None:
                evidence[f'{arm}_{role}']={'channel':name,'field':field,'metadata':meta,
                    'source_mapping':result.source_mapping,'alignment':result.params,
                    'source_unit':source_unit,'output_unit':unit,'indices':spec.get('indices'),
                    **({'input_order':order,'configured_order':spec.get('order'),
                        'framework_normalized':normalized,'output_order':'xyzw'} if role=='quaternion' else {})}
        T=np.asarray(bindings['T_arm_to_camera'],float)
        if T.shape!=(4,4) or not np.isfinite(T).all() or not np.allclose(T[3],[0,0,0,1]):raise ValueError('Explicit arm-to-camera transform required')
        if not np.allclose(T[:3,:3].T@T[:3,:3],np.eye(3),atol=1e-4) or not np.isclose(np.linalg.det(T[:3,:3]),1,atol=1e-4):
            raise ValueError('Arm-to-camera transform must contain a proper rotation')
        data[f'{arm}_T']=T
    if not data:raise ValueError('At least one calibrated arm is required')
    return data


def depth_array(episode,params,shape,destination=None):
    cfg=params['depth'];item=cfg['episodes'][episode.id]
    if cfg.get('unit') not in {'m','mm'} or cfg.get('source') not in {'sensor_depth','monocular_estimate'}:
        raise ValueError('Explicit depth units and source required')
    if cfg.get('registered_to_rgb') is not True or not cfg.get('registration_id'):
        raise ValueError('Verified RGB-depth registration required')
    if (not item.get('correspondence_evidence') or resolve_channel(episode,item.get('rgb_channel'))
            !=resolve_channel(episode,params['views']['faceImg'])):
        raise ValueError('Provide correspondence evidence for the configured RGB channel')
    x=np.load(item['path'],allow_pickle=False,mmap_mode='r')
    selected=params.get('frame_range',[0,episode.channels[resolve_channel(episode,params['views']['faceImg'])].count])
    if item.get('frame_range', [0,episode.channels[resolve_channel(episode,params['views']['faceImg'])].count])!=selected:
        raise ValueError('Depth frame_range must match the selected original reference frames')
    if x.shape!=shape:raise ValueError('One registered depth map per actual RGB frame is required; no resizing')
    output=(np.lib.format.open_memmap(destination,mode='w+',dtype=np.float32,shape=shape)
            if destination is not None else np.empty(shape,dtype=np.float32))
    for i,frame in enumerate(x):
        if not np.isfinite(frame).all() or np.any(frame<0) or not np.any(frame>0):
            raise ValueError(f'Invalid or entirely missing registered depth at frame {i}')
        output[i]=frame*(0.001 if cfg['unit']=='mm' else 1.0)
    if destination is not None:output.flush()
    return output


class CollisionDetection:
    input_bindings=staticmethod(input_bindings)
    spec=AlgorithmSpec(id='Collision_Detection_Qwen3vl',version='62.2.3',name='Four-event safety detection',
        author='EDC',category='value',description='v62 collision, spill, hand intervention and lid fall.',device='cuda',
        input_descriptions=('EDC RGB frames with recorded time and explicit multi-view correspondence',
            'Calibrated feedback: position, orientation, gripper distance and terminal effort in declared units',
            'Registered per-frame depth with RGB/depth fingerprints and camera intrinsics',
            'Task graph and local Qwen3-VL, SAM3, CoTracker3 and DINOv2 resources in an isolated CUDA worker'),
        parameter_schema={'type':'object','additionalProperties':False,
        'required':['views','models','task','task_graph','arms','depth','intrinsics'], 'properties':{
        'views':{'type':'object','additionalProperties':False,'required':['faceImg'],
                 'properties':{k:{'type':'string'} for k in ('faceImg','leftImg','rightImg')}},
        'models':{'type':'object','additionalProperties':False,'required':['qwen','sam3','cotracker','dinov2'],
                  'properties':{k:{'type':'string'} for k in ('qwen','sam3','cotracker','dinov2')}},
        'task':{'type':'string'},'task_graph':{'type':'string'},
        'arms':{'type':'object','additionalProperties':False,'properties':{'left':ARM,'right':ARM}},
        'depth':DEPTH,'intrinsics':{'type':'array','minItems':3,
            'items':{'type':'array','minItems':3,'items':{'type':'number'}}},
        'collision_stride':{'type':'integer','minimum':1,'default':3},
        'frame_range':{'type':'array','minItems':2,'items':{'type':'integer','minimum':0}},
        'python':{'type':'string','default':sys.executable},
        'device':{'type':'string','default':'cuda:0'},
        'timeout_seconds':{'type':'integer','minimum':1,'default':86400}}})

    def preflight(self,episode,params):
        try:
            validate_configuration(episode,params)
            for name,path in params['models'].items():
                resource=Path(path)
                valid=resource.is_dir() and any(resource.iterdir()) if name in {'qwen','dinov2'} else resource.is_file() and resource.stat().st_size>0
                if not valid:raise ImportError(f'Provide local {name} model '+('directory' if name in {'qwen','dinov2'} else 'checkpoint file'))
            if params['depth']['source']=='monocular_estimate' and not Path(params['depth']['model']).exists():
                raise ImportError('Depth model path missing for provenance')
            from edc.algorithms.value.runtime import check_runtime
            check_runtime(params,__file__,['torch', 'transformers', 'sam3', 'cotracker', 'cv2','scipy','PIL','open3d'])
            check_device(params)
            manifest=inspect_safety_frames(episode,params)
            physical_arrays(episode,params,manifest['views']['faceImg']['channel'],
                reference_indices=manifest['selected_reference_indices'])
        except ImportError as exc:return 'missing_dependency',str(exc)
        except (ValueError,KeyError,TypeError,IndexError,OSError) as exc:return 'missing_input',str(exc)
        return 'runnable',''

    def run(self,context,params):
        depth_input=params['depth']['episodes'][context.episode.id]
        models={**params['models'],'depth':depth_input['path'],
                'depth_correspondence':depth_input['correspondence_evidence'],
                'task_graph':params['task_graph']}
        if params['depth']['source']=='monocular_estimate':models['depth_model']=params['depth']['model']
        provenance=save_provenance(context,params,__file__,models,['embodied-data-curation','torch','transformers','sam3','cotracker','numpy','scipy','opencv-python','opencv-python-headless','Pillow'],shared_modules=('edc.execution.frames','edc.data.transforms.resampling','edc.data.ir.canonical','edc.data.ir.units'))
        manifest=inspect_safety_frames(context.episode,params,getattr(context,'upstream',None))
        work=Path(context.workdir)
        context.write_result('input-mapping.json',manifest)
        name=manifest['views']['faceImg']['channel']
        selected=manifest['selected_reference_indices']
        times=np.asarray(manifest['views']['faceImg']['source_timestamps'],float)[selected]
        cache_frames(context.episode,manifest,work/'frames',range(manifest['frame_count']))
        # Only the native visual scanner needs video. Its presentation time is
        # relative source time, with every encoded frame mapped back to originals.
        from PIL import Image
        from edc.execution.frames import PreparedFrames
        prepared=PreparedFrames(manifest)
        def original_rgb(index):
            with Image.open(prepared.image_path('faceImg',index)) as image:
                return np.asarray(image.convert('RGB'))
        first=original_rgb(0)
        rgb=np.lib.format.open_memmap(work/'rgb.npy',mode='w+',dtype=np.uint8,
            shape=(manifest['frame_count'],*first.shape))
        for i in range(len(rgb)):rgb[i]=original_rgb(i)
        rgb.flush()
        if len(rgb)!=len(times):raise ValueError('Decoded frame count differs from mapping')
        from .depth_evidence import validate_correspondence
        validate_correspondence(context.episode,params,manifest,rgb,work/'depth-validation.json')
        depth_array(context.episode,params,rgb.shape[:3],work/'depth.npy')
        presentation=render_videos(manifest,work/'input',times,fps=30)
        views={role:row['path'] for role,row in presentation['views'].items()}
        physical_mapping={}
        arrays=physical_arrays(context.episode,params,name,physical_mapping,reference_indices=selected)
        context.write_result('physical-mapping.json',physical_mapping)
        arrays.update(K=np.asarray(params['intrinsics']),timestamps=times)
        np.savez_compressed(Path(context.workdir)/'physical-input.npz',**arrays)
        state=[]
        for i in range(len(rgb)):
            row={}
            for arm in params['arms']:
                row[f'follow_{arm}_gripper']=float(arrays[f'{arm}_gripper'][i,0])*100
                row[f'follow_{arm}_joint_effort']=[float(arrays[f'{arm}_terminal_effort'][i,0])]
            state.append(row)
        # State windows use relative source seconds, matching the derived video.
        # Keep the original samples and fixed physical thresholds.
        context.write_result('input/edc-physical-state.json',{'timestamps':(times-times[0]).tolist(),
            'time_source':manifest['views']['faceImg']['time_source'],
            'data':state,'gripper_unit':'cm','terminal_effort_unit':'N*m',
            'source':'EDC aligned feedback','mapping':'../input-mapping.json'})
        result=run_worker(context,params,__package__,{'episode_id':context.episode.id,'views':views})
        return {**result,'provenance':provenance}

plugin=CollisionDetection()
