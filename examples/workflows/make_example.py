"""Generate an explicitly synthetic native-HDF5 workflow demonstration."""
import argparse
from pathlib import Path
import numpy as np
import h5py
import yaml


def generate(output):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    data = root / 'source'
    data.mkdir()
    entries = []
    rng = np.random.default_rng(20261026)
    for i in range(2):
        name = f'episode_{i}.h5'
        with h5py.File(data / name, 'w') as handle:
            handle['head_rgb'] = rng.integers(30, 180, size=(12, 24, 32, 3), dtype=np.uint8)
            handle['joint_pos'] = np.linspace(0, 90, 24, dtype=np.float64).reshape(12, 2)
            handle['timestamp'] = np.arange(12, dtype=np.float64) / 10
        entries.append({'id': f'episode_{i}', 'path': name, 'format': 'hdf5',
                        'selector': {'group': '/'}, 'metadata': {'synthetic': True}})
    profile = {'schema_version': 1, 'id': 'synthetic_workflow', 'version': '1',
        'timestamp_column': 'timestamp', 'timestamp_unit': 's',
        'camera_requirements': {'head': ['rgb']}, 'channels': {
            'head_rgb': {'modality': 'rgb', 'sensor_id': 'head', 'logical_name': 'rgb/head',
                         'time_source': 'synthetic', 'representation': {'camera_role': 'head'}},
            'joint_pos': {'modality': 'joint_space', 'fields': ['position'], 'unit': 'deg',
                'representation': {'quantity': 'joint_angle', 'semantic_status': 'confirmed'},
                'source_kind': 'feedback', 'calibration_status': 'confirmed', 'side': 'right',
                'logical_name': 'joint/arm/right/feedback', 'time_source': 'synthetic'}}}
    (data / 'robot.yaml').write_text(yaml.safe_dump(profile, allow_unicode=True))
    (data / 'dataset.yaml').write_text(yaml.safe_dump({'schema_version': 1,
        'id': 'synthetic_workflow', 'version': '1', 'episodes': entries,
        'tasks': ['合成接口验证，不是真实采集或训练收益验证']}, allow_unicode=True))
    stages = [
        {'id': 'quality', 'kind': 'quality', 'input': 'source', 'algorithms': ['Structure_Check']},
        {'id': 'edit', 'kind': 'editing', 'input': 'quality',
            'algorithms': ['team_example__image_edit'], 'select': {'all': True}},
        {'id': 'recheck', 'kind': 'quality', 'input': 'edit', 'algorithms': ['Structure_Check']},
        {'id': 'value', 'kind': 'value', 'input': 'recheck',
            'algorithms': ['team_example__dataset_inventory']},
        {'id': 'augment', 'kind': 'augmentation', 'input': 'value', 'select': {'all': True},
            'algorithms': ['team_example__image_augment'],
            'params': {'team_example__image_augment': {'variants': 2}}},
        {'id': 'aug_recheck', 'kind': 'quality', 'input': 'augment', 'algorithms': ['Structure_Check']}]
    config = {'schema_version': 'dqs_workflow_v1', 'id': 'synthetic_curation',
              'dataset': {'path': 'source'}, 'stages': stages}
    target = root / 'workflow.yaml'
    target.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    print(generate(parser.parse_args().output))
