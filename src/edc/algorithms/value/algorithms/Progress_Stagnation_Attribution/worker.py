"""GRM environment entry point. All writes stay in the EDC artifact directory."""
import json
from pathlib import Path
import sys
import numpy as np
from edc.reporting.results import write_json


def attribute_physical(signals, timeline, native, params, *, fps, input_error=None):
    """Run the same physical stage for fresh or explicitly reused visual curves."""
    from .attributor import EpisodeAttributor
    attr = EpisodeAttributor(str(native.parent / 'input'), fps, signals=signals, timeline=timeline,
                             load_rules=params.get('load_rules'),
                             evidence_policy=params.get('evidence_policy', 'confirmed_only'))
    attribution = attr.attribute_from_assessment(str(native / 'evaluation_summary.json'))
    limitations = {arm: attr.gfeat.assessment_limitations(arm, 0, timeline.count-1)
                   for arm in ('left', 'right')}
    if input_error or signals.warnings:
        limitations['input'] = ([input_error] if input_error else []) + signals.warnings
    component = {'status': 'incomplete' if any(limitations.values()) else 'completed',
                 'limitations': limitations, 'evidence_policy': attr.gfeat.evidence_policy,
                 'evidence_status': 'provisional' if any(attr.gfeat.assumptions.values()) else 'confirmed_only',
                 'assumptions': attr.gfeat.assumptions}
    return attr, attribution, component


def execute(request):
    from .evaluator import SubtaskTrajectoryEvaluator
    from .signals import PhysicalSignals, validate_evidence_policy
    from .algorithm import validate_timing
    work=Path(request['workdir']); params=request['params']; native=work/'native'
    from .load_rules import validate_load_configuration
    validate_load_configuration(params)
    validate_evidence_policy(params.get('evidence_policy', 'confirmed_only'))
    native.mkdir()
    manifest=request.get('frame_manifest')
    timeline=validate_timing(params,manifest or request['views'])
    write_json(native/'frame-timeline.json',timeline.describe())
    components={name:{'status':'pending'} for name in ('segmentation','visual','physical','filter')}
    if params.get('filter_policy','none')=='none': components['filter']={'status':'not_requested'}
    result={'evaluation':None,'attribution':None,'frame_timeline':timeline.describe(),
            'filter_policy':params.get('filter_policy','none'),'assessment_complete':False,'components':components}
    def finish():
        for name, state in components.items():
            if state['status'] == 'pending':
                components[name] = {'status': 'not_run', 'reason': 'An earlier required component is incomplete'}
        result['assessment_complete']=all(x['status'] in {'completed','not_requested'} for x in components.values())
        from .reporting import save_attribution_report
        save_attribution_report(result, native, timeline)
        write_json(work/'native-result.json',result)
        return result
    data=json.loads((work/'physical-signals.json').read_text())
    for roles in data['arms'].values():
        for entry in roles.values():
            for key in ('values','openness'):
                if key in entry and entry[key] is not None: entry[key]=np.asarray(entry[key],float)
    signals=PhysicalSignals(data['episode_id'],np.asarray(data['timestamps']),data['arms'],data['warnings'])
    result['physical_signals']=signals.describe()
    from .segmentation import resolve_subtasks
    from .timeline import normalize_subtasks
    if 'subtasks' in params:
        subtasks=normalize_subtasks(params['subtasks'],timeline,params.get('subtask_time_basis'))
    else:
        if data.get('input_error') or timeline.source is None or (manifest and not manifest['coverage_complete']):
            components['segmentation']={'status':'incomplete','reason':data.get('input_error') or 'Source timeline or paired image coverage unavailable'}
            return finish()
        try:
            from .segmentation import feature_payload
            feature_payload(signals,30)
        except (ValueError,KeyError) as exc:
            components['segmentation']={'status':'incomplete','reason':str(exc)}
            return finish()
        subtasks=resolve_subtasks(params,signals,request.get('views'),work,manifest=manifest,timeline=timeline)
    components['segmentation']={'status':'completed','intervals':subtasks}
    write_json(native/'subtasks.json',subtasks)
    if manifest:
        from edc.execution.frames import PreparedFrames
        from .timeline import sampled_indices
        requested=sampled_indices(subtasks,params['frame_interval'])
        missing={role:[i for i in requested if row['reference_indices'][i] is None] for role,row in manifest['views'].items()}
        if any(missing.values()):
            components['visual']={'status':'incomplete','missing_reference_indices':missing}
            return finish()
        frames=PreparedFrames(manifest)
    else: frames=None
    evaluator=SubtaskTrajectoryEvaluator(params['model'],subtasks,str(work/'input'),str(native),
        frame_interval=params['frame_interval'],mode=params['mode'],task_name=params['task'],gpu_memory_utilization=params['gpu_memory_utilization'],
        timeline=timeline,frame_source=frames,export_video=params.get('export_video',False))
    evaluator.evaluate_all_subtasks_with_filtering()
    if not (native/'pred_vllm.json').is_file(): raise RuntimeError('Combined progress curve missing')
    evaluation=json.loads((native/'evaluation_summary.json').read_text())
    result['evaluation']=evaluation
    visual_complete=manifest is None or manifest['coverage_complete']
    components['visual']={'status':'completed' if visual_complete else 'incomplete','coverage_complete':visual_complete}
    # Persist a partial report before physical processing; exceptions cannot erase it.
    write_json(native/'partial-result.json',result)
    if timeline.source is None or len(signals.timestamps) != timeline.count:
        components['physical']={'status':'incomplete','reason':data.get('input_error') or 'Recorded timeline unavailable'}
        return finish()
    attr, attribution, components['physical'] = attribute_physical(
        signals, timeline, native, params, fps=evaluator.fps, input_error=data.get('input_error'))
    result['load_rules'] = attr.gfeat.load_rules
    result['load_sources'] = attr.gfeat.load_evidence
    write_json(native/'attribution_result.json',attribution)
    result['attribution']=attribution
    limitations = components['physical']['limitations']
    write_json(native/'partial-result.json',result)
    if params['filter_policy']=='training_filter_v8':
        if any(limitations.values()):
            components['filter']={'status':'incomplete','limitations':limitations}
            return finish()
        if attr.gfeat.load_rules['unit'] == 'N*m':
            components['filter']={'status':'incomplete','reason':'training_filter_v8 current rules are not calibrated for torque'}
            return finish()
        if any(arm not in attr.gfeat.proactive_inputs for arm in ('left','right')):
            components['filter']={'status':'incomplete','reason':'training_filter_v8 native-scale calibration unavailable'}
            return finish()
        from .offline_v3 import refine_episode
        from .decision_export_v4 import convert_progress_episode
        from .training_filter_v8 import convert
        refined=refine_episode(work/'input',native/'v3',native,attributor=attr)
        def current(_episode,arm,frame,window=15):
            values=attr.gfeat.cur_rms[arm][timeline.offset(frame,-0.5):frame]
            return float(values.mean()) if len(values) and np.isfinite(values).all() else None
        d,o,errors=convert_progress_episode(work/'input',native/'v3',native,pre_open_current=current,timeline=timeline)
        if errors: raise ValueError(f'Filter prerequisites incomplete: {errors}')
        d,o,attempts,errors,comparisons=convert(refined,evaluation,d,o,attr.grip_events,timeline=timeline)
        filtering={'decisions':d,'observations':o,'attempts':attempts,'errors':errors,'comparisons':comparisons,
                   'application':'suggestions_only','policy_version':'training_filter_v8'}
        write_json(native/'training_filter_v8.json',filtering)
        if errors: raise ValueError('Some filter intervals could not be resolved; see training_filter_v8.json')
        result['filter_suggestions']=filtering
        components['filter']={'status':'completed'}
    return finish()

if __name__=='__main__': execute(json.loads(Path(sys.argv[1]).read_text()))
