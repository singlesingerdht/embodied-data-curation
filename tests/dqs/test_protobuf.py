"""Embedded descriptors, payload presence and image layout are reader contracts."""
from types import SimpleNamespace
import numpy as np
import pytest

pb = pytest.importorskip('google.protobuf.descriptor_pb2')
pytest.importorskip('mcap')
from framework.readers.protobuf import message_class, image
from dqs import open_dataset, run


def schema():
    files = pb.FileDescriptorSet()
    root = files.file.add(name='robot.proto', package='demo', syntax='proto3')
    root.dependency.append('joints.proto')
    state = root.message_type.add(name='State')
    state.field.add(name='joints', number=1, type=11, type_name='.demo.Joints', label=1)
    dep = files.file.add(name='joints.proto', package='demo', syntax='proto3')
    joints = dep.message_type.add(name='Joints')
    joints.field.add(name='position', number=1, type=1, label=3)
    return SimpleNamespace(encoding='protobuf', name='demo.State', data=files.SerializeToString())


def write_mcap(path, sc, payloads, summary):
    from mcap.writer import Writer, IndexType
    with path.open('wb') as stream:
        writer = Writer(stream, index_types=IndexType.ALL if summary else IndexType.NONE,
                        repeat_channels=summary, repeat_schemas=summary,
                        use_statistics=summary, use_summary_offsets=summary)
        writer.start()
        sid = writer.register_schema(name=sc.name, encoding=sc.encoding, data=sc.data)
        cid = writer.register_channel(topic='/robot/state', message_encoding='protobuf', schema_id=sid)
        for i, payload in enumerate(payloads):
            writer.add_message(channel_id=cid, log_time=1_000_000_000+i*10_000_000,
                               publish_time=0, data=payload)
        writer.finish()


@pytest.mark.parametrize('summary', [True, False])
def test_dynamic_numeric_through_public_ir_and_executor(tmp_path, summary):
    sc = schema(); cls = message_class(sc)
    payloads = [cls(joints={'position': [i*.01, i*.02]}).SerializeToString() for i in range(10)]
    path = tmp_path/'vendor.mcap'; write_mcap(path, sc, payloads, summary)
    original = path.read_bytes()
    with open_dataset(path) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            c = ep.channels['/robot/state']
            assert c.fields == ['joints.position']
            assert c.time_source == 'container' and c.unit is None
            np.testing.assert_allclose(ep.read_signal(c.name, 'joints.position')[1], [.01,.02])
            assert ep.read_values(c.name, []) == []
        result = run(ds, algorithm_ids=['Numeric_Check'], output=tmp_path/'results')
        assert result['status_counts'] == {'completed': 1}
    assert path.read_bytes() == original


def test_absent_message_does_not_become_zero_joint_values(tmp_path):
    sc = schema(); cls = message_class(sc)
    path = tmp_path/'missing.mcap'
    write_mcap(path, sc, [cls(joints={'position':[1,2]}).SerializeToString(), cls().SerializeToString()], True)
    with open_dataset(path) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            with pytest.raises(ValueError, match='missing field'):
                ep.read_signal('/robot/state','joints.position')


def image_schema():
    files=pb.FileDescriptorSet()
    file=files.file.add(name='image.proto', package='foxglove', syntax='proto3')
    msg=file.message_type.add(name='RawImage')
    for number, (name, typ) in enumerate([('width',7),('height',7),('step',7),('encoding',9),('data',12)],1):
        msg.field.add(name=name,number=number,type=typ,label=1)
    return SimpleNamespace(encoding='protobuf',name='foxglove.RawImage',data=files.SerializeToString())


def test_padded_bgr_image_through_mcap(tmp_path):
    sc=image_schema(); cls=message_class(sc)
    msg=cls(width=2,height=1,step=8,encoding='bgr8',data=bytes([1,2,3,4,5,6,0,0]))
    path=tmp_path/'image.mcap'; write_mcap(path,sc,[msg.SerializeToString()],True)
    with open_dataset(path) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            frame=next(ep.iter_frames('/robot/state'))
            np.testing.assert_array_equal(frame['image'], [[[3,2,1],[6,5,4]]])
            assert frame['source_time']==1.0
    msg.step=5
    with pytest.raises(ValueError,match='stride'):image(msg)
    msg.step=8;msg.encoding='16UC1'
    with pytest.raises(ValueError,match='encoding'):image(msg)


def test_missing_descriptor_dependency_rejected():
    sc=schema(); files=pb.FileDescriptorSet.FromString(sc.data)
    del files.file[1:]
    sc.data=files.SerializeToString()
    with pytest.raises(ValueError,match='dependency'):message_class(sc)
