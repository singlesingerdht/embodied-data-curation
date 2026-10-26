"""Explicit image/video inputs over unchanged quality methods and thresholds."""
import time


def input_mode(episode, channel, params):
    """Select from reader-declared storage, never from a topic name."""
    mode = params.get('input_mode', 'auto')
    if mode not in {'auto', 'frames', 'video'}:
        raise ValueError(f'Invalid input_mode: {mode}')
    if mode == 'auto':
        storage = getattr(episode, 'image_storage', None)
        mode = storage(channel.name) if callable(storage) else 'unknown'
        if mode not in {'frames', 'video'}:
            raise ValueError(f'{channel.name}: native storage unknown; select input_mode explicitly')
    if mode == 'frames' and params.get('weights'):
        raise ValueError('Video composite weights cannot apply to frame-only checks; omit weights or use input_mode=video')
    return mode


def assess(episode, params, context):
    reports = []
    requested_mode = params.get('input_mode', 'auto')
    for channel in episode.channels.values():
        if channel.modality != 'rgb' or not channel.image_available:
            continue
        started = time.perf_counter()
        role = (channel.representation or {}).get('camera_role', 'unknown')
        mode = None
        try:
            mode = input_mode(episode, channel, params)
            result = _assess_channel(episode, channel, {**params, 'input_mode': mode}, context, len(reports), started, role)
        except Exception as exc:
            missing = isinstance(exc, ImportError) or type(exc).__name__ == 'DependencyMissing'
            result = {'score': None, 'passed': None, 'verdict': 'NOT_APPLICABLE',
                'assessment_complete': False,
                'processing_status': 'missing_dependency' if missing else 'processing_error',
                'ir_unavailable': {'dependency_missing' if missing else 'error': f'{type(exc).__name__}: {exc}'},
                'reasons': [f'{type(exc).__name__}: {exc}']}
        result.update(input_channel=channel.name, input=str(episode.path),
            camera_role=role, camera_role_status=(channel.representation or {}).get('camera_role_status', 'unknown'),
            side=channel.side, threshold_set='camera_agnostic', input_mode=mode, requested_input_mode=requested_mode,
            adapter_elapsed_s=time.perf_counter()-started,
            source_mapping={'episode_id': getattr(episode, 'id', None),
                'episode_selector': getattr(episode, 'selector', {}),
                'channel': channel.name, 'source': channel.source,
                'mapping': channel.source_mapping})
        reports.append(result)
    if not reports:
        raise ValueError('No selected readable RGB channel')
    return reports


def _assess_channel(episode, channel, params, context, index, started, role):
    from Quality_Assessment.algorithms.Video_Integrity import run_check
    from Quality_Assessment.algorithms.Video_Integrity.frame_quality import FrameQualityDetector
    mode = params['input_mode']
    if mode == 'frames':
        fq = FrameQualityDetector(sample_step=params.get('sample_step', 5),
            downscale=params.get('downscale', 4)).detect_frames(
                episode.iter_frames(channel.name), channel.count)
        measured_failure = fq.get('verdict') == 'DO_NOT_USE'
        measured_warning = fq.get('verdict') == 'NEEDS_REVIEW'
        result = {'score': None, 'passed': False if measured_failure else None,
            'verdict': 'DO_NOT_USE' if measured_failure else 'NEEDS_REVIEW' if measured_warning else 'OBSERVATION',
            'assessment_complete': False, 'frame_quality': fq,
            'inspection_object': 'source_decoded_frames',
            'encoding_integrity_scope': 'Source pixel decode only; no independent encoded-byte validation',
            'container_checks': {'status': 'not_applicable',
                'reason': 'Frame input has no video container; packet PTS and video frame-count checks are not applied'},
            'reasons': ['原始解码画面已检查；视频容器及原始编码字节独立完整性未评估'],
            'process_time_ms': round((time.perf_counter()-started)*1000, 1)}
    else:
        directory = getattr(context, 'workdir', None)
        materialize = getattr(context, 'materialize_video', episode.materialize_video)
        path = (materialize(channel.name, directory / f'rgb-{index}.mp4')
                if directory is not None else episode.video_source(channel.name))
        result = run_check.assess(path, cam_type=role, weights=params.get('weights'),
            sample_step=params.get('sample_step', 5), downscale=params.get('downscale', 4))
        evidence = getattr(context, 'video_evidence', episode.video_evidence)
        result.update(evidence(channel.name, inspection_input=path))
        result['adapter_elapsed_s'] = time.perf_counter()-started
    return result
