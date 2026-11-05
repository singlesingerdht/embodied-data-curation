"""Video_Integrity report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""
from edc.algorithms.quality.shared.verdicts import frames_only

GUIDE = '每 5 帧抽 1 帧检查。某一类问题（如模糊、遮挡）占抽检帧的 30% 及以上判排除；不到 30% 判通过，格子里备注问题帧数。'  # Shown above this algorithm's problems in the report.


QUALITY = {'black_ratio': '黑屏', 'white_ratio': '白屏', 'garbled_ratio': '花屏', 'freeze_ratio': '画面冻结',
           'occlusion_ratio': '遮挡', 'blur_ratio': '模糊', 'overexpo_ratio': '过曝', 'underexpo_ratio': '欠曝',
           'color_anomaly_ratio': '偏色'}


def frame_counts(report):
    """'抽检 173 帧（每 5 帧抽 1）中：模糊 16 帧' from the frame check, or '' if no bad frame."""
    quality = report.get('frame_quality') or {}
    total = quality.get('total_frames') or 0
    bad = [f"{QUALITY[k]} {round(quality[k] * total)} 帧" if total and k != 'freeze_ratio'
           else f"{QUALITY[k]} {quality.get('freeze_frames', 0)} 帧" if k == 'freeze_ratio' else f'{QUALITY[k]} {quality[k]:.1%}'
           for k in QUALITY if (quality.get(k) or 0) > 0]
    step = (quality.get('sampling') or {}).get('sample_step')
    head = f'抽检 {total} 帧（每 {step} 帧抽 1）中：' if total and step else '画面问题帧：'
    return head + '，'.join(bad) if bad else ''


def explain(report, labels):
    return frame_counts(report) or '；'.join((report.get('frame_quality') or {}).get('reasons') or [])


def target(report, labels):
    name = report.get('input_channel') or ''
    return labels.get(name, name)


def note(reports, items, labels, common):
    if any(frames_only(r) for r in reports):
        from edc.algorithms.quality.algorithms.Video_Integrity.inputs import VIDEO_STREAM_NOTE
        text = VIDEO_STREAM_NOTE if any(r.get('video_stream') for r in reports) else '逐帧图片，无视频容器可查'
        common += ('（' if common else '') + text + ('）' if common else '')
    flagged = [f'{i.target} {frame_counts(r)}' for r, i in zip(reports, items) if i.verdict == 'PASS' and frame_counts(r)]
    return common + ('；通过但有问题帧：' + '；'.join(flagged) if flagged else '')
