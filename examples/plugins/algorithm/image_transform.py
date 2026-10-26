"""Synthetic HDF5 wiring examples, not validated robot repair/augmentation methods."""
import copy
from pathlib import Path
import shutil
import numpy as np
import yaml
from dqs.registry import AlgorithmSpec
from dqs import dataset_reference


class ImageTransform:
    def __init__(self, category):
        self.category = category
        self.spec = AlgorithmSpec('team_example__image_' + ('edit' if category == 'editing' else 'augment'),
            '1', '合成图像变换接线示例', 'Example team',
            'HDF5 synthetic fixture only: explicit brightness offsets; no claim of repair or training benefit.',
            category=category, dependencies=('h5py',),
            parameter_schema={'type': 'object', 'additionalProperties': False, 'properties': {
                'channel': {'type': 'string', 'default': 'rgb/head'},
                'delta': {'type': 'integer', 'minimum': -64, 'maximum': 64, 'default': 4},
                'variants': {'type': 'integer', 'minimum': 1, 'maximum': 4, 'default': 1}}},
            data_requirements=({'id': 'image', 'channel_param': 'channel', 'modality': 'rgb'},))

    def preflight(self, episode, params):
        return ('runnable', '') if episode.format == 'hdf5' and episode.metadata.get('synthetic') is True else (
            'missing_input', 'Example transform accepts explicitly synthetic HDF5 fixtures only')

    def run(self, context, params):
        import h5py
        ep = context.episode
        root = context.workdir / 'dataset'
        root.mkdir()
        channel = params['channel']
        entries, mappings = [], []
        frames = [value['image'] for value in ep.iter_frames(channel)]
        for index in range(params['variants']):
            ident = ep.id + '__' + self.category + '_' + str(index)
            name = f'variant_{index}.h5'
            target = root / name
            shutil.copyfile(ep.path, target)
            # Native copy preserves every untouched dtype, field, value and timestamp.
            # Reading RGB goes through the common Channel interface.
            with h5py.File(target, 'r+') as handle:
                group = handle[ep.selector.get('group', '/')]
                original = np.asarray(frames)
                changed = np.clip(original.astype(np.int16) + params['delta'] * (index + 1), 0, 255).astype(original.dtype)
                group[channel][...] = changed
            entries.append({'id': ident, 'path': name, 'format': 'hdf5',
                'selector': copy.deepcopy(ep.selector), 'metadata': copy.deepcopy(ep.metadata)})
            mappings.append({'output_episode_id': ident, 'parent_episode_id': ep.id,
                             'rows': 'identity', 'changed_channels': [channel]})
        (root / 'robot.yaml').write_text(yaml.safe_dump(context.dataset.profile, allow_unicode=True))
        (root / 'dataset.yaml').write_text(yaml.safe_dump({'schema_version': 1,
            'id': context.dataset.id + '__' + self.category, 'version': context.workdir.name,
            'tasks': context.dataset.tasks, 'episodes': entries,
            'metadata': {'synthetic': True, 'purpose': 'interface demonstration only'}}, allow_unicode=True))
        return {'output': str(root), 'params': params,
                'source_mapping': {'parent_fingerprint': dataset_reference(context.dataset)['fingerprint'],
                                   'episodes': mappings},
                'scope': 'synthetic wiring example; no measured repair or augmentation benefit'}


editing_plugin = ImageTransform('editing')
augmentation_plugin = ImageTransform('augmentation')
