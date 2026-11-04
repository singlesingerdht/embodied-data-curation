"""Optional freeze/motion evidence, independent of the image method's decision."""
from pathlib import Path
import numpy as np


def _curve(path, times, values):
    """A standalone, dependency-free plot. Values/rows remain available in the JSON."""
    x = (times-times[0]) / max(float(times[-1]-times[0]), 1e-12) * 560 + 30
    low, high = float(values.min()), float(values.max())
    span = max(high-low, 1e-12)
    lines = []
    for j in range(values.shape[1]):
        y = 185-(values[:, j]-low)/span*155
        points = ' '.join(f'{a:.2f},{b:.2f}' for a,b in zip(x,y))
        color = ('#2563eb', '#dc2626', '#16a34a', '#9333ea')[j%4]
        lines.append(f'<polyline fill="none" stroke="{color}" points="{points}"/>')
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="225">'
        '<rect width="100%" height="100%" fill="white"/>'
        '<text x="30" y="18">Position (SI); time relative to interval start (s)</text>'
        + ''.join(lines) + f'<text x="30" y="215">0 — {times[-1]-times[0]:.4g} s; '
        f'range {low:.4g} … {high:.4g}</text></svg>', encoding='utf-8')


def attach(result, camera, episode, motions, params, context, camera_index):
    """`episode` is the complete SI execution view; the camera check itself stays scoped."""
    evidence = {'informational_only': True, 'status': 'available', 'segments': [],
        'interpretation': 'Repeated pixels and robot motion are evidence for human review; motion may be outside the view.'}
    result['freeze_motion_review'] = evidence
    candidates = (result.get('frame_quality') or {}).get('freeze_segments') or []
    if not candidates:
        evidence.update(status='no_candidates', reason='No freeze candidates from the existing frame check')
        return
    from edc.robots.scope import selected_arms, source_arms
    from edc.data.transforms import RobotTransforms
    profile = episode.profile
    arms = selected_arms(profile, params.get('arms', 'all')) if profile.get('parts') else []
    times = np.asarray(camera.timestamps if camera.timestamps is not None else [], dtype=float)
    if (len(times) != camera.count or not camera.clock or camera.time_source not in {'acquisition', 'container'}
            or not np.isfinite(times).all() or np.any(np.diff(times) <= 0)):
        evidence.update(status='unavailable', reason='Camera requires a valid recorded timeline and declared clock')
        return
    directory = Path(context.workdir) / f'freeze-motion-{camera_index}'
    directory.mkdir(parents=True, exist_ok=True)
    sample_indices = set()
    for k, candidate in enumerate(candidates):
        a, b = int(candidate['start']), int(candidate['end'])
        if not 0 <= a <= b < len(times):
            evidence.update(status='unavailable', reason='Freeze interval has no identity source-frame mapping')
            continue
        row = {'candidate': dict(candidate), 'camera_rows': [a, b],
            'time_range_s': [float(times[a]), float(times[b])], 'clock': camera.clock,
            'time_source': camera.time_source, 'motion': [], 'image_samples': []}
        samples = sorted({a, (a+b)//2, b})
        row['sample_rows'] = samples
        sample_indices.update(samples)
        for name in motions:
            item = {'channel': name, 'status': 'unavailable'}
            row['motion'].append(item)
            try:
                c = episode.channels.get(name)
                if c is None:
                    raise ValueError('Motion feedback channel is missing')
                owners = source_arms(profile, name, c, episode.channels)
                if arms and owners and not owners & set(arms):
                    item.update(status='not_checked', reason='unselected_arm')
                    continue
                if c.modality not in {'joint_space', 'pose'} or 'position' not in c.fields:
                    raise ValueError('Motion input must be joint/pose position feedback')
                spec = c.field_spec('position')
                if spec.get('source_kind') != 'feedback':
                    raise ValueError('Motion input is not confirmed feedback')
                if arms and not owners:
                    raise ValueError('Motion arm ownership is missing')
                unit = spec.get('unit')
                if unit not in ({'m'} if c.modality == 'pose' else {'rad', 'm'}):
                    raise ValueError('Motion position requires SI units (m or rad)')
                if not c.clock or c.time_source not in {'acquisition', 'container'} or c.time_source != camera.time_source:
                    raise ValueError('Comparable recorded time sources and declared clocks required')
                mapped = RobotTransforms(profile).clock(c.timestamps, c.clock, camera.clock, time_source=c.time_source)
                mt = np.asarray(mapped.output)
                if not len(mt) or not np.isfinite(mt).all() or np.any(np.diff(mt) <= 0):
                    raise ValueError('Motion timeline must be finite and strictly increasing')
                indices = np.flatnonzero((mt >= times[a]) & (mt <= times[b]))
                if len(indices) < 2:
                    raise ValueError('Fewer than two motion samples within candidate interval')
                data = np.asarray(episode.read_signal(name, 'position'))
                if data.ndim == 1:
                    data = data[:, None]
                if data.ndim != 2 or len(data) != len(mt) or not np.isfinite(data[indices]).all():
                    raise ValueError('Motion signal shape or finite-value validation failed')
                # Curves are explicitly sampled for evidence size; original source row numbers survive.
                stride = max(1, int(np.ceil(len(indices)/1000)))
                plotted = np.unique(np.r_[indices[::stride], indices[-1]])
                path = directory / f'interval-{k}-motion-{len(row["motion"])-1}.svg'
                _curve(path, mt[plotted], data[plotted])
                item.update(status='available', unit=unit, arms=sorted(owners), clock_mapping=mapped.params,
                    source=c.source, source_mapping=c.source_mapping, selection=c.selection,
                    original_sample_count=len(indices), curve_stride=stride, source_rows=plotted.tolist(),
                    times_s=mt[plotted].tolist(), positions=data[plotted].tolist(),
                    component_range=np.ptp(data[indices], axis=0).tolist(), curve=str(path.relative_to(context.workdir)))
            except (ValueError, TypeError, KeyError) as exc:
                item['reason'] = str(exc)
        row['status'] = 'available' if any(m['status'] == 'available' for m in row['motion']) else 'unavailable'
        evidence['segments'].append(row)
    if not any(r['status'] == 'available' for r in evidence['segments']):
        evidence.update(status='unavailable', reason='No eligible motion feedback could be associated; image result retained')
    # Decode original input only for selected stills, never encode a preview video.
    import cv2
    saved = {}
    try:
        for frame in episode.iter_frames(camera.name):
            index = frame['index']
            if index in sample_indices:
                path = directory / f'frame-{index}.png'
                if not cv2.imwrite(str(path), np.asarray(frame['image'])[:, :, ::-1]):
                    raise ValueError('Could not save image evidence')
                saved[index] = {'source_row': index, 'time_s': float(times[index]),
                    'path': str(path.relative_to(context.workdir))}
            if sample_indices and index >= max(sample_indices):
                break
    except Exception as exc:
        evidence['image_sample_reason'] = f'{type(exc).__name__}: {exc}'
    for row in evidence['segments']:
        row['image_samples'] = [saved[i] for i in row.pop('sample_rows') if i in saved]
    context.write_result(f'freeze-motion-{camera_index}/evidence.json', evidence)
