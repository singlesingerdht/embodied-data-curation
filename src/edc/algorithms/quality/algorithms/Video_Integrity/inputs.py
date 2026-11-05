"""Explicit image/video inputs over unchanged quality methods and thresholds."""
import time

# 相机消息是 H.264 视频流（一条消息一帧压缩数据）时的说明，报告格子也用这句
VIDEO_STREAM_NOTE = '视频流（H.264），已逐帧解码检查画面；没有 MP4 文件，文件层检查不适用'


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
        motion_mapping = params.get('freeze_motion_review') or {}
        if channel.name in motion_mapping:
            from .freeze_review import attach
            # Optional association must not convert a completed image check into a failure.
            try:
                attach(result, channel, context.episode, motion_mapping[channel.name], params, context, len(reports))
            except Exception as exc:
                result['freeze_motion_review'] = {'status': 'unavailable', 'informational_only': True,
                    'reason': f'{type(exc).__name__}: {exc}'}
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
    from edc.algorithms.quality.algorithms.Video_Integrity import method
    from edc.algorithms.quality.algorithms.Video_Integrity.frame_quality import FrameQualityDetector
    mode = params['input_mode']
    if mode == 'frames':
        fq = FrameQualityDetector(sample_step=params.get('sample_step', 5),
            downscale=params.get('downscale', 4)).detect_frames(
                episode.iter_frames(channel.name), channel.count)
        measured_failure = fq.get('verdict') == 'DO_NOT_USE'
        try:  # 只为读消息类型；没有话题信息的数据（如测试用的假数据）按普通图片写
            topic = episode.describe_legacy().topics.get(channel.name)
        except AttributeError:
            topic = None
        video_stream = (getattr(topic, 'msg_type', None) or '').endswith('/CompressedVideo')
        if fq.get('verdict') == 'NEEDS_REVIEW':
            # Pass or exclude only (user decision 2026-10-30): a few bad frames below the method's
            # exclusion ratio pass, and the report notes how many. The method's verdict is kept.
            fq = {**fq, 'method_verdict': fq['verdict'], 'verdict': 'ACCEPTABLE'}
        # Image-only storage has no container to check, so the frame check is the whole assessment.
        result = {'score': None, 'passed': not measured_failure,
            'verdict': 'DO_NOT_USE' if measured_failure else 'ACCEPTABLE',
            'assessment_complete': True, 'frame_quality': fq,
            'inspection_object': 'source_decoded_frames',
            'encoding_integrity_scope': 'Source pixel decode only; no independent encoded-byte validation',
            'container_checks': {'status': 'not_applicable',
                'reason': 'Frame input has no video container; packet PTS and video frame-count checks are not applied'},
            'video_stream': video_stream,
            'reasons': [VIDEO_STREAM_NOTE if video_stream else '相机存的是逐帧图片：已抽帧解码检查画面；没有视频容器，容器检查不适用'],
            'process_time_ms': round((time.perf_counter()-started)*1000, 1)}
    else:
        directory = getattr(context, 'workdir', None)
        materialize = getattr(context, 'materialize_video', episode.materialize_video)
        path = (materialize(channel.name, directory / f'rgb-{index}.mp4')
                if directory is not None else episode.video_source(channel.name))
        result = method.assess(path, cam_type=role, weights=params.get('weights'),
            sample_step=params.get('sample_step', 5), downscale=params.get('downscale', 4))
        evidence = getattr(context, 'video_evidence', episode.video_evidence)
        result.update(evidence(channel.name, inspection_input=path))
        result['adapter_elapsed_s'] = time.perf_counter()-started
        # Pass or exclude only, as for frame input (user decision 2026-10-30): anything the method does not
        # exclude passes; the report notes the bad-frame counts. The method's verdicts are kept.
        if result.get('verdict') == 'NEEDS_REVIEW':
            result.update(method_verdict='NEEDS_REVIEW', verdict='ACCEPTABLE', passed=True)
        fq = result.get('frame_quality')
        if result.get('verdict') != 'DO_NOT_USE' and isinstance(fq, dict) and fq.get('verdict') == 'NEEDS_REVIEW':
            result['frame_quality'] = {**fq, 'method_verdict': fq['verdict'], 'verdict': 'ACCEPTABLE'}
    return result
