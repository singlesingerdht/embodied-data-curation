import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from edc.data.ir.dataset import open_dataset
from edc.robots.profiles import load_profile
from edc.robots.validation import check_robot
from edc.algorithms.registry import Registry

ROOT=Path(__file__).resolve().parents[2]


def demo(tmp_path):
    spec=importlib.util.spec_from_file_location('topic_demo',ROOT/'examples/onboarding/make_demo.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.generate(tmp_path/'input')


def test_hand_and_multifield_metadata_survive_ir(tmp_path):
    with open_dataset(demo(tmp_path)) as dataset:
        assert check_robot(dataset)==[]
        with next(dataset.iter_episodes()).opened() as episode:
            hand=episode.channels['/robot/left_hand/state']
            assert hand.body_part=='hand' and hand.side=='left'
            assert hand.field_spec('position')['unit']=='rad'
            assert hand.field_spec('current')['unit']=='A'
            assert hand.field_spec('current')['source_kind']=='feedback'
            assert hand.calibration_status=='pending' # Readable does not mean grasp calibrated.
            assert len(hand.joint_names)==6
            assert episode.read_signal(hand.name,'current').shape==(40,6)
            with pytest.raises(ValueError,match='Field not provided'):
                episode.read_signal(hand.name,'effort')


def test_joint_names_must_match_signal_dimensions(tmp_path):
    with open_dataset(demo(tmp_path)) as dataset:
        dataset.profile['channels']['/robot/left_hand/state']['joint_names']=['only_one']
        assert any('joint_names dimension mismatch' in i for i in check_robot(dataset))


@pytest.mark.parametrize('patch',[
    {'side':'guess'}, {'field_metadata':{'effort':{'unit':'A'}}},
    {'field_metadata':{'position':{'source_kind':'maybe'}}},
    {'dimensions':[-1]}, {'joint_names':['a','a']}])
def test_profile_rejects_invalid_semantics(patch):
    with pytest.raises(ValueError):
        load_profile({'schema_version':1,'id':'robot','version':'1',
                      'channels':{'hand':{'fields':['position'],**patch}}})


def test_external_plugin_namespace_required(monkeypatch):
    plugin=SimpleNamespace(spec=SimpleNamespace(id='OfficialLookingName',interface_version=1))
    monkeypatch.setattr('edc.algorithms.registry.entry_points',lambda group:[SimpleNamespace(name='bad',load=lambda:plugin)])
    with pytest.raises(ValueError,match='team__algorithm'):
        Registry('edc.algorithms').discover()
