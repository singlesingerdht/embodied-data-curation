"""Explicit orientation conversions; NumPy only, with unchanged source rows.

RPY uses right-handed, fixed XYZ axes: R = Rz(yaw) Ry(pitch) Rx(roll).
Matrices act on column vectors. These are representation conversions within
the same frames, not coordinate-frame transformations or calibration.
"""
import copy
import numpy as np

from .core import TransformResult


def orientation_spec(spec):
    """Validate a complete representation; never infer it from array length."""
    if not isinstance(spec, dict):
        raise ValueError('Orientation representation must be explicitly declared')
    if spec.get('handedness', 'right') != 'right' or spec.get('vectors', 'column') != 'column':
        raise ValueError('Orientation conversion supports explicitly right-handed column-vector conventions')
    kind = spec.get('kind')
    if kind == 'quaternion':
        if spec.get('order') not in {'xyzw', 'wxyz'}:
            raise ValueError('Quaternion requires explicit order: xyzw or wxyz')
        if spec.get('unit', '1') != '1':
            raise ValueError('Quaternion unit must be dimensionless (1)')
        return {'kind': kind, 'order': spec['order'], 'unit': '1'}
    if kind == 'rpy':
        if spec.get('axes') != 'xyz' or spec.get('convention') != 'extrinsic':
            raise ValueError('RPY requires axes: xyz and convention: extrinsic (fixed axes)')
        if spec.get('unit') not in {'rad', 'deg'}:
            raise ValueError('RPY requires explicit rad or deg unit')
        return {k: spec[k] for k in ('kind', 'axes', 'convention', 'unit')}
    if kind == 'rotation_matrix':
        if spec.get('convention', 'active') != 'active':
            raise ValueError('Rotation matrix requires active column-vector convention')
        layout = spec.get('layout')
        if layout not in {'matrix', 'row_major', 'column_major'}:
            raise ValueError('Rotation matrix requires layout: matrix, row_major or column_major')
        if spec.get('unit', '1') != '1':
            raise ValueError('Rotation matrix unit must be dimensionless (1)')
        return {'kind': kind, 'layout': layout, 'unit': '1'}
    raise ValueError('Unknown orientation representation; declare quaternion, rpy or rotation_matrix')


def sample_shape(spec):
    spec = orientation_spec(spec)
    if spec['kind'] == 'quaternion':
        return (4,)
    if spec['kind'] == 'rpy':
        return (3,)
    return (3, 3) if spec['layout'] == 'matrix' else (9,)


def source_orientation_metadata(channel, message_type):
    """ROS schema establishes encoding, never physical calibration or clock."""
    schemas = {'sensor_msgs/Imu', 'geometry_msgs/Pose', 'geometry_msgs/PoseStamped',
               'geometry_msgs/PoseWithCovariance', 'geometry_msgs/PoseWithCovarianceStamped',
               'nav_msgs/Odometry'}
    if message_type.replace('/msg/', '/') not in schemas or 'orientation' not in channel.fields:
        return
    declared = channel.field_metadata.get('orientation', {}).get('representation') or channel.representation
    expected = {'kind': 'quaternion', 'order': 'xyzw', 'unit': '1'}
    if declared.get('kind') not in (None, 'unknown') and orientation_spec(declared) != expected:
        raise ValueError(f'{channel.name}: ROS orientation schema is quaternion xyzw; use an explicit transform')
    item = channel.field_metadata.setdefault('orientation', {})
    if item.get('unit', channel.unit) not in (None, 'unknown', '1'):
        raise ValueError(f'{channel.name}: quaternion schema conflicts with orientation field unit')
    if item.get('dimensions') not in (None, [4]):
        raise ValueError(f'{channel.name}: ROS orientation schema requires dimensions [4]')
    if item.get('dimensions') is None:
        item['dimensions'] = [4]
    item['representation'] = expected
    item['representation_evidence'] = {'source': 'message_schema', 'message_type': message_type}


def _matrix(values, spec, tolerance):
    shape = sample_shape(spec)
    if values.ndim != len(shape) + 1 or values.shape[1:] != shape:
        raise ValueError(f'Orientation requires (N, {shape}) source shape; got {values.shape}')
    if spec['kind'] == 'rotation_matrix':
        result = values.reshape(-1, 3, 3).copy()
        if spec['layout'] == 'column_major':
            result = result.transpose(0, 2, 1).copy()
        if (not np.allclose(result.transpose(0, 2, 1) @ result, np.eye(3), atol=tolerance, rtol=0)
                or not np.allclose(np.linalg.det(result), 1, atol=tolerance, rtol=0)):
            raise ValueError('Rotation matrix must be orthonormal with determinant +1; automatic repair is forbidden')
        return result
    if spec['kind'] == 'rpy':
        angles = np.deg2rad(values) if spec['unit'] == 'deg' else values
        r, p, y = angles.T
        cr, cp, cy = np.cos(r), np.cos(p), np.cos(y)
        sr, sp, sy = np.sin(r), np.sin(p), np.sin(y)
        return np.array([[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                         [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
                         [-sp, cp*sr, cp*cr]]).transpose(2, 0, 1)
    q = values[:, [1, 2, 3, 0]] if spec['order'] == 'wxyz' else values.copy()
    norms = np.linalg.norm(q, axis=1)
    if not np.allclose(norms, 1, atol=tolerance, rtol=0):
        raise ValueError('Quaternion must have unit norm; automatic repair is forbidden')
    q = q / norms[:, None]  # floating-point roundoff within the declared tolerance only
    x, y, z, w = q.T
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]).transpose(2, 0, 1)


def _quaternion(matrices):
    # Symmetric eigenproblem remains stable at half turns (trace near -1).
    result = np.empty((len(matrices), 4))
    for i, m in enumerate(matrices):
        xx, xy, xz, yx, yy, yz, zx, zy, zz = m.ravel()
        k = np.array([[xx-yy-zz, xy+yx, xz+zx, zy-yz],
                      [xy+yx, yy-xx-zz, yz+zy, xz-zx],
                      [xz+zx, yz+zy, zz-xx-yy, yx-xy],
                      [zy-yz, xz-zx, yx-xy, xx+yy+zz]]) / 3
        q = np.linalg.eigh(k)[1][:, -1]
        # Deterministic sign; this does not unwrap quaternion trajectories.
        sign_index = 3 if abs(q[3]) > 1e-12 else int(np.argmax(np.abs(q[:3])))
        result[i] = q if q[sign_index] >= 0 else -q
    return result


def convert_orientation(values, *, source, target, tolerance=1e-6):
    """Convert an N-sample orientation array and return auditable evidence."""
    if isinstance(tolerance, bool) or not np.isfinite(tolerance) or not 0 < tolerance <= 1e-3:
        raise ValueError('Orientation tolerance must be finite and in (0, 1e-3]')
    source, target = orientation_spec(source), orientation_spec(target)
    raw = np.asarray(values)
    if raw.dtype.kind not in 'fiu' or not np.isfinite(raw).all():
        raise ValueError('Orientation conversion requires finite numeric values; inspect raw invalid rows separately')
    matrices = _matrix(raw.astype(float, copy=True), source, tolerance)
    singular = []
    if target['kind'] == 'rotation_matrix':
        result = matrices.copy()
        if target['layout'] == 'column_major':
            result = result.transpose(0, 2, 1)
        if target['layout'] != 'matrix':
            result = result.reshape(len(raw), 9).copy()
    elif target['kind'] == 'quaternion':
        result = _quaternion(matrices)
        if target['order'] == 'wxyz':
            result = result[:, [3, 0, 1, 2]]
    else:
        m = matrices
        cp = np.hypot(m[:, 0, 0], m[:, 1, 0])
        pitch = np.arctan2(-m[:, 2, 0], cp)
        roll = np.arctan2(m[:, 2, 1], m[:, 2, 2])
        yaw = np.arctan2(m[:, 1, 0], m[:, 0, 0])
        mask = cp <= 1e-10
        singular = np.flatnonzero(mask).tolist()
        roll[mask] = np.arctan2(-m[mask, 1, 2], m[mask, 1, 1])
        yaw[mask] = 0
        result = np.column_stack((roll, pitch, yaw))
        if target['unit'] == 'deg':
            result = np.rad2deg(result)
    return TransformResult(result, {
        'operation': 'orientation', 'source_representation': source, 'target_representation': target,
        'tolerance': tolerance, 'invalid_policy': 'reject', 'frame_transform': False,
        'quaternion_sign': 'canonical_nonnegative_w_then_largest_xyz',
        'gimbal_lock_rows': singular, 'gimbal_lock_policy': 'yaw=0; equivalent rotation retained',
        'source_unchanged': True, 'trajectory_unwrapped': False,
    }, {'rows': 'identity', 'elements': 'representation_conversion'})


def orientation(episode, channel, *, target, field='orientation', tolerance=1e-6):
    episode.open()
    from edc.algorithms.channel_names import resolve_channel
    from .episode import provenance
    name = resolve_channel(episode, channel)
    c = episode.channels[name]
    spec = c.field_spec(field)
    source = copy.deepcopy(spec.get('representation') or c.representation)
    if 'unit' not in source and spec.get('unit') is not None:
        source['unit'] = spec['unit']
    normalized = orientation_spec(source)
    if spec.get('unit') not in (None, 'unknown', normalized['unit']):
        raise ValueError('Orientation representation unit conflicts with field unit')
    result = convert_orientation(episode.read_signal(name, field), source=source, target=target, tolerance=tolerance)
    result.source_mapping = {**provenance(episode, name, field),
        'requested_channel': channel, 'elements': 'representation_conversion',
        'original_source_mapping': copy.deepcopy(c.source_mapping),
        'time_source': c.time_source, 'clock': c.clock,
        'coordinate_frame': spec['coordinate_frame'], 'frame_evidence': copy.deepcopy(c.frame_evidence)}
    return result
