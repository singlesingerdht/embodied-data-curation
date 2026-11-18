"""Explicit opt-in for the native Gemini alignment method."""
import copy
import os
from pathlib import Path
import numpy as np
from edc.reporting.results import write_json


def validate_intervals(subtasks, duration):
    previous = 0.0
    if not subtasks:
        raise ValueError('At least one task interval is required')
    steps = set()
    for item in subtasks:
        if (not item.get('query') or item['step'] in steps
                or not previous <= item['clip_start_sec'] < item['clip_end_sec'] <= duration + 1e-6):
            raise ValueError('Task intervals must be unique, ordered, non-overlapping and within the video')
        steps.add(item['step'])
        previous = item['clip_end_sec']


def feature_payload(signals, fps):
    rows = [{} for _ in signals.timestamps]
    roles = {'hand_position': 'gripper', 'ee_position': 'position',
             'arm_load': 'joint_cur', 'joint_position': 'joint_position'}
    for arm in ('left', 'right'):
        for role, native in roles.items():
            entry = signals.arms[arm].get(role)
            if not entry or entry['config']['calibration_status'] != 'confirmed':
                raise ValueError(f'Native segmentation requires confirmed {arm}/{role}')
            expected={'ee_position':'m','arm_load':'A','joint_position':'rad'}
            if role in expected and entry['config'].get('unit') != expected[role]:
                raise ValueError(f'Native segmentation requires {role} in {expected[role]}')
            if role == 'hand_position' and not entry['config'].get('segmentation_scale_reference'):
                raise ValueError('Native segmentation needs an explicit gripper scale reference')
            values = np.asarray(entry['values'], float)
            if not np.isfinite(values).all():
                raise ValueError('Segmentation cannot fill missing physical samples')
            if role == 'hand_position':
                if values.shape[1] != 1:
                    raise ValueError('Native segmentation requires scalar calibrated gripper position')
                values = values[:, 0]
            for row, value in zip(rows, values):
                row[f'follow_{arm}_{native}'] = value.tolist()
    return {'fps': fps, 'data': rows, 'timestamps': (signals.timestamps-signals.timestamps[0]).tolist()}


def resolve_subtasks(params, signals, views, work, *, manifest=None, timeline=None):
    if manifest is not None:
        from edc.execution.frames import render_videos
        presentation=render_videos(manifest,Path(work)/'segmentation-video',timeline.source)
        views=presentation['views'];fps=presentation['fps'];duration=presentation['duration_sec']
    else:
        presentation=None
        fps = views['cam_high']['fps']
        duration = len(views['cam_high']['mapped_timestamps']) / fps
    if 'subtasks' in params:
        validate_intervals(params['subtasks'], duration)
        return params['subtasks']
    cfg = params['segmentation']
    if cfg.get('method') != 'gemini' or cfg.get('allow_remote_upload') is not True:
        raise ValueError('Gemini segmentation requires explicit remote-upload opt-in')
    from google import genai
    from . import segmenter as native
    work = Path(work) / 'segmentation'
    work.mkdir()
    features = feature_payload(signals, fps)
    write_json(work / 'physical-features.json', features)
    plots = native.generate_feature_plots(features, fps, work)
    description, images = native.feature_images_description(plots)
    client = genai.Client(api_key=os.environ[cfg['api_key_env']])
    uploaded = []
    try:
        for role in ('cam_high', 'cam_left_wrist', 'cam_right_wrist'):
            uploaded.append(native._upload_and_wait_ready(client, Path(views[role]['path'])))
        result = native.align_subtasks_with_gemini(
            client=client, video_files=uploaded,
            image_parts=native.load_feature_images([str(p) for p in images]),
            task_config={'task_name': params['task'], 'description': cfg['description'], 'subtasks': cfg['subtasks']},
            event_log=native.extract_gripper_events(features, fps),
            movement_log=native.extract_movement_events(features, fps),
            feature_images_desc=description, video_duration=duration, model=cfg['model'],
            response_path=work / 'raw-response.txt')
        write_json(work / 'raw-segmentation.json', result)
        expected = [x['query'] for x in cfg['subtasks']]
        if [x.get('query') for x in result.get('subtasks', [])] != expected:
            raise ValueError('Segmentation changed the requested task definitions')
        # Preserve raw evidence, but do not fabricate absent or invalid model intervals.
        for item in result.get('subtasks', []):
            bounds = [item.get('clip_start_sec'), item.get('clip_end_sec')]
            if (any(not isinstance(x, (int, float)) or isinstance(x, bool) or not np.isfinite(x) for x in bounds)
                    or not 0 <= bounds[0] < bounds[1] <= duration + 0.001):
                raise ValueError('Segmentation did not provide valid evidence-backed intervals')
        result = native.enforce_continuity(copy.deepcopy(result), video_fps=fps, video_duration=duration)
        result['review_warnings'] = native.review_subtask_balance(result, duration)
        write_json(work / 'segmentation.json', result)
        if presentation is not None:
            result['subtasks'][-1]['clip_end_sec']=duration
        validate_intervals(result['subtasks'], duration)
        if result['review_warnings'] or result.get('_removed'):
            raise ValueError('Segmentation needs review; provide confirmed explicit subtasks to continue')
        if presentation is not None:
            mapped=[]
            indices=presentation['reference_indices']; times=np.asarray(presentation['presentation_times'])
            for item in result['subtasks']:
                start=min(int(np.searchsorted(times,item['clip_start_sec'],side='left')),len(indices)-1)
                end=min(int(np.searchsorted(times,item['clip_end_sec'],side='right'))-1,len(indices)-1)
                first,last=indices[start],indices[end]
                if last<=first: raise ValueError('Segmentation interval has fewer than two original images')
                mapped.append({**item,'_start_frame':first,'_end_frame':last,'_time_basis':'source',
                    'source_start_sec':timeline.time_at(first),'source_end_sec':timeline.time_at(last),
                    'presentation_start_frame':start,'presentation_end_frame':end})
            write_json(work/'mapped-subtasks.json',mapped)
            return mapped
        return result['subtasks']
    finally:
        for item in uploaded:
            try:
                client.files.delete(name=item.name)
            except Exception as exc:
                with (work / 'remote-cleanup-errors.txt').open('a') as log:
                    log.write(f'{item.name}: {type(exc).__name__}\n')
