"""EDC GRM progress and calibrated physical attribution adapter."""
from pathlib import Path
import sys
import numpy as np
from edc.algorithms.registry import AlgorithmSpec
from edc.algorithms.channel_names import resolve_channel
from edc.algorithms.value.runtime import save_provenance, run_worker
from .timeline import FrameTimeline
from .load_rules import LOAD_RULE_SCHEMA, validate_load_configuration
from .signals import validate_evidence_policy


def validate_timing(params, rows):
    if 'schema' in rows:
        return FrameTimeline.from_manifest(rows)
    row = rows['cam_high']
    return FrameTimeline(row['mapped_timestamps'], row.get('fps'))


def progress_views(episode, params):
    from edc.execution.frames import inspect_frames
    return inspect_frames(episode, {role: params['views'][role] for role in
        ('cam_high', 'cam_left_wrist', 'cam_right_wrist')})


def physical_inputs(episode, params, reference):
    from .signals import PhysicalSignals, Signal, hand_rule_values
    from edc.data.transforms.core import convert_units
    arms = {'left': {}, 'right': {}}
    warnings = []
    target = np.asarray(episode.channels[reference].timestamps, float)
    supported = {'hand_position', 'hand_load', 'arm_load', 'ee_position', 'arm_velocity', 'joint_position'}
    for arm, roles in params['physical_signals'].items():
        if arm not in arms:
            raise ValueError('Physical arm binding must be left or right')
        for role, spec in roles.items():
            if role not in supported:
                raise ValueError(f'Unsupported physical role: {role}')
            if 'proxy_of' in spec:
                continue
            try:
                name = resolve_channel(episode, spec['channel']); field = spec['field']
                c = episode.channels[name]; semantic = c.field_spec(field)
                if semantic['source_kind'] != 'feedback' or spec.get('source_kind', 'feedback') != 'feedback':
                    raise ValueError('Attribution requires feedback, not command signals')
                source_unit = semantic['unit']
                unit = spec.get('unit', 'm' if role == 'ee_position' else source_unit)
                if source_unit in ('unknown', None, '') or unit in ('unknown', None, ''):
                    raise ValueError('Physical units are unknown in the robot profile')
                if c.time_source not in {'acquisition', 'container'} or not c.clock:
                    raise ValueError('Physical signals need source timestamps and clock')
                calibration = semantic['calibration_status']
                if spec.get('calibration_status', calibration) != calibration:
                    raise ValueError('Algorithm calibration must match the robot profile')
                aligned = episode.align_signal(name, field, reference, method='linear', max_gap=spec.get('max_gap_sec'))
                converted = convert_units(aligned.output, source_unit, unit)
                values = np.asarray(converted.output, float)
                if values.ndim == 1: values = values[:, None]
                if 'indices' in spec: values = values[:, spec['indices']]
                if role == 'ee_position' and (values.shape[1] != 3 or unit != 'm'):
                    raise ValueError('End-effector translation requires three coordinates in metres')
                cfg = {**spec, 'unit': unit, 'source_kind': 'feedback', 'calibration_status': calibration,
                       'quantity': semantic.get('quantity', 'unknown'),
                       'calibration_reference': semantic.get('calibration_reference')}
                entry = {'values': values, 'config': cfg,
                         'description': Signal(target, values, {**cfg, 'source_unit': source_unit,
                             'conversion': converted.params, 'source_mapping': aligned.source_mapping}).describe()}
                arms[arm][role] = entry
                if role == 'hand_position': entry['openness'] = hand_rule_values(values, cfg)
            except (ValueError, KeyError, OSError) as exc:
                warnings.append(f'{arm}.{role}: {exc}')
        for role, spec in roles.items():
            if 'proxy_of' not in spec:
                continue
            if role != 'hand_load' or spec['proxy_of'] != 'arm_load' or not spec.get('reference'):
                raise ValueError('Only an explicit hand_load proxy_of arm_load with reference is supported')
            source = arms[arm].get('arm_load')
            if source is None:
                warnings.append(f'{arm}.hand_load: declared arm_load proxy is unavailable')
                continue
            arms[arm][role] = {**source, 'config': {**source['config'], 'proxy_of': 'arm_load', 'reference': spec['reference']},
                'description': {**source['description'], 'proxy_of': 'arm_load', 'reference': spec['reference']}}
    return PhysicalSignals(episode.id, target, arms, warnings)


class ProgressAttribution:
    spec = AlgorithmSpec(id='Progress_Stagnation_Attribution', version='1.3.2',
        name='Task progress and stagnation attribution', author='EDC', category='value',
        description='Native GRM multi-view progress, physical attribution and optional training_filter_v8.',
        device='optional_cuda', parameter_schema={'type':'object','additionalProperties':False,
        'required':['views','model','task','physical_signals'], 'properties':{
            'views':{'type':'object','required':['cam_high','cam_left_wrist','cam_right_wrist'],
                     'additionalProperties':False,'properties':{k:{'type':'string'} for k in ('cam_high','cam_left_wrist','cam_right_wrist')}},
            'model':{'type':'string'}, 'task':{'type':'string'},
            'subtasks':{'type':'array','minItems':1,'items':{'type':'object','required':['step','query'],
                'properties':{'step':{'type':'integer'},'query':{'type':'string'},'clip_start_sec':{'type':'number','minimum':0},'clip_end_sec':{'type':'number','exclusiveMinimum':0},'clip_start_frame':{'type':'integer','minimum':0},'clip_end_frame':{'type':'integer','minimum':1}}}},
            'physical_signals':{'type':'object'},
            'load_rules':LOAD_RULE_SCHEMA,
            'evidence_policy':{'type':'string','enum':['confirmed_only','allow_provisional'],
                               'default':'confirmed_only'},
            'segmentation':{'type':'object','additionalProperties':False,
                'required':['method','model','api_key_env','allow_remote_upload','description','subtasks'],
                'properties':{'method':{'type':'string','enum':['gemini']},'model':{'type':'string'},
                    'api_key_env':{'type':'string'},'allow_remote_upload':{'type':'boolean'},
                    'description':{'type':'string'},'subtasks':{'type':'array','minItems':1,
                        'items':{'type':'object','required':['step','query'],
                            'properties':{'step':{'type':'integer'},'query':{'type':'string'}}}}}},
            'filter_policy':{'type':'string','enum':['none','training_filter_v8'],'default':'none'},
            'max_frame_gap_sec':{'type':'number','exclusiveMinimum':0,'description':'Deprecated and ignored; quality policy belongs to the quality stage.'},
            'subtask_time_basis':{'type':'string','enum':['playback','source']},
            'export_video':{'type':'boolean','default':False},
            'keep_frame_cache':{'type':'boolean','default':False},
            'frame_interval':{'type':'integer','minimum':1,'default':10},
            'mode':{'type':'string','enum':['average','forward','backward','incremental'],'default':'average'},
            'gpu_memory_utilization':{'type':'number','exclusiveMinimum':0,'maximum':1,'default':0.8},
            'python':{'type':'string','default':sys.executable},
            'timeout_seconds':{'type':'integer','minimum':1,'default':86400}}})

    def preflight(self, episode, params):
        if not Path(params['model']).is_dir(): return 'missing_dependency', 'Provide local GRM model directory'
        try:
            validate_load_configuration(params)
            validate_evidence_policy(params.get('evidence_policy', 'confirmed_only'))
            from edc.algorithms.value.runtime import check_runtime
            check_runtime(params,__file__,['torch','transformers','vllm','PIL','matplotlib'] +
                          (['moviepy'] if params.get('export_video',False) else []) +
                          (['google.genai','av'] if 'segmentation' in params else []),
                          checks=(__package__ + '.model_config.check_versions',))
            manifest = progress_views(episode, params)
            timeline = validate_timing(params,manifest)
            if ('subtasks' in params) == ('segmentation' in params):
                raise ValueError('Select explicit subtasks or native segmentation, exclusively')
            if 'subtasks' in params:
                from .timeline import normalize_subtasks
                normalize_subtasks(params['subtasks'],timeline,params.get('subtask_time_basis'))
            elif params['segmentation'].get('allow_remote_upload') is not True:
                raise ValueError('Remote segmentation requires explicit upload permission in configuration')
        except ImportError as exc: return 'missing_dependency',str(exc)
        except (ValueError,KeyError,OSError) as exc: return 'missing_input',str(exc)
        return 'runnable', ''

    def run(self, context, params):
        from edc.execution.frames import inspect_frames, cache_frames
        from .timeline import normalize_subtasks, sampled_indices
        import warnings
        validate_load_configuration(params)
        validate_evidence_policy(params.get('evidence_policy', 'confirmed_only'))
        provenance = save_provenance(context, params, __file__, {'grm':params['model']},
            ['embodied-data-curation','torch','transformers','vllm','google-genai','numpy','av','moviepy','matplotlib','Pillow'], shared_modules=('edc.execution.frames',))
        manifest = inspect_frames(context.episode, params['views'], upstream=getattr(context,'upstream',None))
        timeline = validate_timing(params,manifest)
        notices=[]
        if any('delta_threshold' in spec for roles in params['physical_signals'].values()
               for role,spec in roles.items() if role in {'arm_load','hand_load'}):
            notice='delta_threshold uses legacy load normalization; prefer confirmed current in A and fixed load_rules'
            warnings.warn(notice,FutureWarning);notices.append(notice)
        if 'max_frame_gap_sec' in params:
            notice='max_frame_gap_sec is deprecated and ignored; upstream quality policy applies'
            warnings.warn(notice,FutureWarning);notices.append(notice)
        manifest['warnings']=notices
        context.write_result('frame-timeline.json',timeline.describe())
        context.write_result('input-mapping.json',manifest)
        if 'subtasks' in params:
            tasks=normalize_subtasks(params['subtasks'],timeline,params.get('subtask_time_basis'))
            indices=sampled_indices(tasks,params.get('frame_interval',10))
        else:
            indices=range(manifest['frame_count'])
        manifest=cache_frames(context.episode,manifest,Path(context.workdir)/'input',indices)
        physical_error=None
        try:
            if timeline.source is None:
                raise ValueError('Recorded time required for complete physical attribution')
            signals=physical_inputs(context.episode,params,manifest['views']['cam_high']['channel'])
            data={'episode_id':signals.episode_id,'timestamps':signals.timestamps,'arms':signals.arms,'warnings':signals.warnings}
        except (ValueError,KeyError) as exc:
            physical_error=str(exc);data={'episode_id':context.episode.id,'timestamps':[],
                'arms':{'left':{},'right':{}},'warnings':[physical_error]}
        data['input_error']=physical_error
        context.write_result('physical-signals.json',data)
        result=run_worker(context,params,__package__,{'frame_manifest':manifest,'episode_id':context.episode.id})
        from .cache import finalize_cache
        result = finalize_cache(context.workdir, manifest, result, keep=params.get('keep_frame_cache', False))
        return {**result,'provenance':provenance}

plugin = ProgressAttribution()
