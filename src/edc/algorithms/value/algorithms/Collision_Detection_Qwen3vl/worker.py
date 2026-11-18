"""Run complete native physical candidates then v62 visual scanning/adjudication."""
import json
from pathlib import Path
import sys
from edc.reporting.results import write_json
from .algorithm import EVENTS


def check_completion(report,verdicts):
    capabilities=report.get('safety_capabilities',{})
    checks={k:{'status':'completed' if capabilities.get(k,{}).get('enabled') else 'not_assessed',
               'reason':capabilities.get(k,{}).get('reason','')} for k in EVENTS}
    if report.get('scan_coverage',{}).get('direct_scan_complete') is not True:
        for k in ('spill','hand_intervention','lid_fall'):checks[k]['status']='incomplete'
    if any(x.get('verdict') not in {'confirmed','rejected','needs_review'} or
           any('adjudication unavailable' in str(reason) for reason in x.get('limitations',[]))
           for x in verdicts):
        for row in checks.values():row['status']='incomplete'
    return checks


def execute(request):
    from .run_four_event_pipeline import main,PIPELINE_REVISION
    work=Path(request['workdir']);p=request['params'];out=work/'native'
    import torch
    if not p['device'].startswith('cuda'):
        raise ValueError('The frozen v62 physical method requires an explicitly selected CUDA device')
    torch.cuda.set_device(torch.device(p['device']))
    from .safety_framework import LocalQwenVisionModel
    model=LocalQwenVisionModel(p['models']['qwen'],p['device'])
    model.evidence_dir=out/'model-requests'
    status=main(['--episode',str(work/'input'),'--out',str(out),'--task',p['task'],
        '--local-model',p['models']['qwen'],'--local-device',p['device'],'--task-graph',p['task_graph'],
        '--primary-camera','faceImg','--collision-stride',str(p['collision_stride']),'--no-visualization','--no-evidence-clips'],model=model)
    if status:raise RuntimeError(f'v62 pipeline failed: {status}')
    report=json.loads((out/'safety_report.json').read_text())
    verdicts=json.loads((out/'events_adjudicated.json').read_text())
    checks=check_completion(report,verdicts)
    counts={key:sum(v.get('verdict')==key for v in verdicts)
            for key in ('confirmed','needs_review','rejected')}
    result={'method_revision':PIPELINE_REVISION,'native':report,'checks':checks,
            'event_counts':counts,
            'assessment_complete':all(c['status']=='completed' for c in checks.values())}
    if result['assessment_complete']:
        result['verdict']='FAIL' if counts['confirmed'] else 'NEEDS_REVIEW' if counts['needs_review'] else 'PASS'
        result['reason']=(f"Four event checks completed; confirmed {counts['confirmed']}, "
                          f"review {counts['needs_review']}, rejected {counts['rejected']}. "
                          'Judgment applies only to the selected frames.')
    mapping=work/'input-mapping.json'
    if mapping.is_file():
        manifest=json.loads(mapping.read_text())
        selected=manifest['selected_reference_indices']
        result['assessment_scope']={'source_episode':manifest['source'],
            'selector':manifest['selector'],'original_frame_range':[selected[0],selected[-1]+1],
            'source_frame_count':manifest['source_frame_count'],
            'whole_episode':len(selected)==manifest['source_frame_count'],
            'time_basis':'seconds relative to first selected source frame',
            'mapping':'input-mapping.json'}
    write_json(work/'native-result.json',result)
    if not result['assessment_complete']:raise RuntimeError('Four-event assessment incomplete; see native-result.json')
    return result

if __name__=='__main__':execute(json.loads(Path(sys.argv[1]).read_text()))
