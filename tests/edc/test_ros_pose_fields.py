"""ROS pose schemas expose separate XYZ/XYZW fields, including standalone MCAP."""
import hashlib
import numpy as np
import pytest
from edc import open_dataset, run


@pytest.mark.parametrize('storage', ['standalone_mcap', 'ros1', 'ros2_sqlite'])
@pytest.mark.parametrize('message_type', [
    'geometry_msgs/msg/PoseStamped', 'nav_msgs/msg/Odometry',
])
def test_decoded_pose_fields_never_enter_flat_layout_inference(tmp_path, storage, message_type):
    pytest.importorskip('rosbags')
    from rosbags.typesys import Stores, get_typestore
    store = get_typestore(Stores.ROS1_NOETIC if storage == 'ros1' else Stores.ROS2_HUMBLE)
    if storage == 'ros1':
        from rosbags.rosbag1 import Writer
        source = tmp_path / 'source.bag'
        writer = Writer(source)
    else:
        from rosbags.rosbag2 import StoragePlugin, Writer
        directory = tmp_path / 'source'
        writer = Writer(directory, version=9, storage_plugin=(
            StoragePlugin.MCAP if storage == 'standalone_mcap' else StoragePlugin.SQLITE3))
        source = directory
    positions = np.array([[0.1, 0.2, 0.3], [0.4, np.nan, 0.6], [0.7, 0.8, 0.9]])
    # Preserve invalid/nonfinite quaternion content for quality checks too.
    orientations = np.array([[0.1, -0.2, 0.3, 0.9], [0.2, 0.3, 0.4, 1.5],
                             [np.inf, -np.inf, 0.5, 0.6]])
    with writer:
        conn = writer.add_connection('/right_arm/end_pose', message_type, typestore=store)
        for index, (xyz, xyzw) in enumerate(zip(positions, orientations)):
            stamp = store.types['builtin_interfaces/msg/Time'](10 + index, 0)
            header = (store.types['std_msgs/msg/Header'](index, stamp, 'base')
                      if storage == 'ros1' else store.types['std_msgs/msg/Header'](stamp, 'base'))
            pose = store.types['geometry_msgs/msg/Pose'](
                store.types['geometry_msgs/msg/Point'](*xyz),
                store.types['geometry_msgs/msg/Quaternion'](*xyzw))
            if message_type.endswith('/Odometry'):
                msg = store.types[message_type](header, 'tool',
                    store.types['geometry_msgs/msg/PoseWithCovariance'](pose, np.zeros(36)),
                    store.types['geometry_msgs/msg/TwistWithCovariance'](
                        store.types['geometry_msgs/msg/Twist'](
                            store.types['geometry_msgs/msg/Vector3'](0., 0., 0.),
                            store.types['geometry_msgs/msg/Vector3'](0., 0., 0.)), np.zeros(36)))
            else:
                msg = store.types[message_type](header, pose)
            raw = (store.serialize_ros1(msg, message_type) if storage == 'ros1'
                   else store.serialize_cdr(msg, message_type))
            writer.write(conn, int((20 + index) * 1e9), raw)
    if storage == 'standalone_mcap':
        source = next(directory.glob('*.mcap'))
    files = list(source.rglob('*')) if source.is_dir() else [source]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    with open_dataset(source) as dataset:
        with next(dataset.iter_episodes()).opened() as episode:
            # Invalid rotations stay auditable; canonical reads fail explicitly.
            with pytest.raises(ValueError, match='finite numeric values'):
                episode.read_signal('/right_arm/end_pose', 'orientation')
            # Read source quaternion first: cached layout must not affect XYZ.
            for field, expected in [('orientation', orientations), ('position', positions),
                                    ('orientation', orientations)]:
                actual = episode.inspect_recorded_signal('/right_arm/end_pose', field)
                np.testing.assert_array_equal(actual, expected)
            np.testing.assert_array_equal(
                episode.read_signal('/right_arm/end_pose', 'position'), positions)
            channel = episode.channels['/right_arm/end_pose']
            assert channel.field_spec('position')['unit'] is None
            assert channel.time_source == 'container'
        summary = run(dataset, algorithm_ids=['Numeric_Check'], output=tmp_path / 'results')
    assert summary['status_counts'] == {'completed': 1}
    for path, before in hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
