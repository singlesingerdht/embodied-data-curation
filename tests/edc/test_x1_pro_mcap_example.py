"""The shipped MCAP example has one consistent, scoped nine-method contract."""
import json
from pathlib import Path

from edc.configuration import load_config, workflow_config
from edc.execution.batch import resolve_robot
from edc.robots.numeric_review import numeric_review


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'configs/x1_pro.yaml'
METHODS = {'Structure_Check', 'Numeric_Check', 'Action_Smoothness', 'Check_Timestamp',
           'Sync_Check', 'Root_Cause', 'Dedup_Check', 'Reset_Detection', 'Video_Integrity'}


def test_example_and_batch_have_identical_robot_and_parameters(monkeypatch):
    monkeypatch.setattr('edc.execution.batch.repo_configs', lambda: ROOT/'configs')
    profile, params = resolve_robot('x1_pro')
    cfg = load_config(CONFIG)
    stage = workflow_config(CONFIG)['stages'][0]
    assert profile == ROOT/'configs/robots/x1_pro.yaml'
    assert json.loads(params.read_text()) == stage['params']
    assert set(stage['algorithms']) == METHODS and stage['selection_policy'] == 'explicit'
    assert len(cfg['dataset']['episodes']) == 1 and cfg['dataset']['tasks'] == []
    assert cfg['dataset']['episodes'][0]['id'] == 'x1_pro_reference_0001'


def test_physical_confirmation_is_scoped_and_clocks_remain_unknown():
    cfg = load_config(CONFIG); p = cfg['robot']
    assert len(p['channels']) == 32
    review = numeric_review(p, cfg['algorithms']['methods']['Numeric_Check']['params'])
    assert review['status'] == 'confirmed' and review['confirmed_by'] == 'data-platform-owner'
    assert len(review['settings']['physical_fields']) == 10
    thresholds = review['settings']['thresholds']
    assert set(thresholds['joint']['limits']) == {'joint/arm/left/feedback', 'joint/arm/right/feedback'}
    assert set(thresholds['command_limits']['limits']) == {'joint/arm/left/command', 'joint/arm/right/command'}
    for name, c in p['channels'].items():
        assert c['clock'] is None
        if c['modality'] == 'joint_space' and c['body_part'] == 'arm':
            assert c['source_kind'] == ('command' if name.startswith('/master_') else 'feedback')
            assert c['field_metadata']['velocity']['unit'] == 'rad/s'
            assert c['field_metadata']['effort']['quantity'] == 'current'
            assert c['field_metadata']['effort']['unit'] is None
    timeline = cfg['algorithms']['methods']['Action_Smoothness']['params']['timeline']
    assert timeline['time_source'] == 'container' and timeline['max_gap_s'] == .1
    assert p['calibration']['Reset_Detection']['schema_version'] == 'edc_reset_calibration_v2'
