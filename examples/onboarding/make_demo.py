"""Generate synthetic topic/hand metadata for integration tests, not hand algorithms."""
import argparse
import json
from pathlib import Path
import yaml


def generate(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    rows = [{"timestamp": i / 20,
             "/robot/arm/joint_states": {"position": [i*.001]*6, "velocity": [.02]*6},
             "/robot/left_hand/state": {"position": [.1,.2,.3,.4,.5,.6], "current": [.02]*6}}
            for i in range(40)]
    source = root / 'episode_001'
    source.mkdir()
    (source / 'trajectory.json').write_text(json.dumps({'data': rows}))
    profile = {'schema_version':1, 'id':'topic_demo_robot', 'version':'1', 'timestamp_unit':'s',
        'parts':{'arm':{'dof':6}, 'left_hand':{'dof':6}},
        'channels':{
            '/robot/arm/joint_states':{
                'modality':'joint_state', 'fields':['position','velocity'],
                'robot_part':'arm', 'body_part':'arm', 'source_kind':'feedback', 'dimensions':[6],
                'time_source':'synthetic', 'clock':'demo',
                'field_metadata':{'position':{'unit':'rad'}, 'velocity':{'unit':'rad/s'}}},
            '/robot/left_hand/state':{
                'modality':'joint_state', 'fields':['position','current'],
                'robot_part':'left_hand', 'body_part':'hand', 'side':'left', 'source_kind':'feedback',
                'dimensions':[6], 'joint_names':[f'hand_joint_{i}' for i in range(6)],
                'calibration_status':'pending', 'time_source':'synthetic', 'clock':'demo',
                'field_metadata':{'position':{'unit':'rad'}, 'current':{'unit':'A'}}}},
        'calibration':{}}
    (root/'robot.yaml').write_text(yaml.safe_dump(profile,sort_keys=False,allow_unicode=True))
    manifest={'id':'synthetic_topic_demo','version':'1','schema_version':1,
              'episodes':[{'id':'episode_001','path':'episode_001','format':'episode'}]}
    (root/'dataset.yaml').write_text(yaml.safe_dump(manifest))
    (root/'finite_params.json').write_text(json.dumps({'team_example__finite_check':{
        'channels':['/robot/arm/joint_states','/robot/left_hand/state']}},indent=2))
    return root


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    print(generate(parser.parse_args().output))
