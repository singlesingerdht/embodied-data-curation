"""Four reusable configurations resolved into one immutable run snapshot."""
import copy
from pathlib import Path
import yaml
from edc.robots.profiles import load_profile
from edc.data.channel_registry import read_unique_yaml


def _component(value, base, stack=()):
    if not isinstance(value, dict):
        raise ValueError('Each configuration component must be a mapping or {ref: file.yaml}')
    if 'ref' not in value:
        return copy.deepcopy(value), base
    if set(value) != {'ref'} or not isinstance(value['ref'], str):
        raise ValueError('A ref component accepts exactly one file reference')
    path = (base / value['ref']).resolve()
    if path in stack:
        raise ValueError('Cyclic configuration reference')
    return _component(read_unique_yaml(path.read_text()), path.parent, (*stack, path))


def load_config(config):
    base = Path.cwd()
    if isinstance(config, (str, Path)):
        path = Path(config).resolve()
        base = path.parent
        config = read_unique_yaml(path.read_text())
    if (not isinstance(config, dict) or config.get('schema_version') != 'edc_config_v1'
            or set(config) - {'schema_version', 'dataset', 'robot', 'algorithms', 'workflow', 'unit_policy'}):
        raise ValueError('Configuration requires edc_config_v1 and dataset/robot/algorithms/workflow')
    resolved = {'schema_version': 'edc_config_v1', 'unit_policy': config.get('unit_policy', 'auto')}
    if resolved['unit_policy'] not in {'auto', 'manual'}:
        raise ValueError('unit_policy must be auto or manual')
    for key in ('dataset', 'robot', 'algorithms', 'workflow'):
        item, origin = _component(config.get(key), base)
        if key == 'dataset':
            if set(item) - {'path', 'backend', 'id', 'version', 'tasks', 'episodes', 'metadata', 'source_bindings', 'capture'}:
                raise ValueError('Unsupported dataset configuration fields')
            if not isinstance(item.get('path'), str):
                raise ValueError('dataset.path is required')
            item['path'] = str((origin / item['path']).resolve())
            if item.get('backend', 'auto') not in {'auto', 'official', 'compat'}:
                raise ValueError('backend must be auto, official or compat')
        if key == 'robot':
            if {'path', 'tasks', 'episodes', 'dataset', 'allowed_tasks'} & set(item):
                raise ValueError('Robot configuration cannot bind a dataset directory or task list')
            # Resolve URDFs relative to the robot file before freezing geometry.
            for chain in item.get('transforms', {}).get('chains', {}).values():
                if 'urdf' in chain:
                    chain['urdf'] = str((origin / chain['urdf']).resolve())
            item = load_profile(item, base=origin)
            from edc.data.channel_registry import load_channel_registry
            item.setdefault('channel_registry', load_channel_registry())
        if key == 'algorithms':
            if set(item) - {'defaults', 'methods'}:
                raise ValueError('algorithms supports defaults and methods')
            defaults = item.setdefault('defaults', {})
            if not isinstance(defaults, dict) or set(defaults) - {'arms', 'selection'}:
                raise ValueError('Algorithm defaults support arms and selection')
            defaults.setdefault('arms', 'all')
            defaults.setdefault('selection', 'runnable')
            if defaults['selection'] not in {'runnable', 'explicit'}:
                raise ValueError('selection must be runnable or explicit')
            if not isinstance(item.setdefault('methods', {}), dict):
                raise ValueError('methods must map algorithm IDs to configuration')
            for name, method in item['methods'].items():
                if (not isinstance(method, dict) or set(method) - {'arms', 'params', 'enabled'}
                        or not isinstance(method.get('params', {}), dict)
                        or type(method.get('enabled', True)) is not bool):
                    raise ValueError(f'{name}: method supports arms, params, enabled')
        resolved[key] = item
    return resolved


def export_config(config, output):
    resolved = load_config(config)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x') as stream:
        yaml.safe_dump(resolved, stream, allow_unicode=True, sort_keys=False)
    return target


def execution_profile(config):
    """Dataset-specific bindings and capture declarations never mutate the robot."""
    profile = copy.deepcopy(config['robot'])
    def check_scope(node):
        if not isinstance(node, dict):
            return
        scope = node.get('applicability')
        if scope is not None:
            if not isinstance(scope, dict) or set(scope) - {'dataset_ids', 'instance_id'}:
                raise ValueError('applicability supports dataset_ids and instance_id')
            if 'dataset_ids' in scope and (not isinstance(scope['dataset_ids'], list) or
                    not scope['dataset_ids'] or any(not isinstance(i, str) or not i for i in scope['dataset_ids'])):
                raise ValueError('applicability.dataset_ids requires nonempty dataset IDs')
            if 'dataset_ids' in scope and config['dataset'].get('id') not in scope['dataset_ids']:
                raise ValueError('A scoped confirmation does not apply to this dataset')
            if 'instance_id' in scope and config['robot'].get('instance_id') != scope['instance_id']:
                raise ValueError('A scoped confirmation does not apply to this robot instance')
        for value in node.values():
            if isinstance(value, dict):
                check_scope(value)
    check_scope(profile)
    bindings = config['dataset'].get('source_bindings', {})
    if not isinstance(bindings, dict):
        raise ValueError('source_bindings maps logical names to actual source channels')
    index = {v.get('logical_name'): k for k, v in profile.get('channels', {}).items() if v.get('logical_name')}
    if set(bindings) - set(index):
        raise ValueError('source_bindings references an undeclared logical name')
    for logical_name, source in bindings.items():
        if source is None:
            raise ValueError(f'Unbound source channel {logical_name}: set dataset.source_bindings to the actual recorded topic/feature')
    remapped = {}
    for source, channel in profile.get('channels', {}).items():
        target = bindings.get(channel.get('logical_name'), source)
        if not isinstance(target, str) or not target or target in remapped:
            raise ValueError('source_bindings must preserve unique source signals')
        remapped[target] = channel
    profile['channels'] = remapped
    capture = config['dataset'].get('capture', {})
    check_scope(capture)
    if not isinstance(capture, dict) or set(capture) - {'channels', 'calibration', 'transforms', 'scope'}:
        raise ValueError('capture supports channels/calibration/transforms/scope')
    if capture and not capture.get('scope'):
        raise ValueError('Capture confirmations require an explicit applicable scope')
    for reference, patch in capture.get('channels', {}).items():
        source = next((k for k, v in remapped.items() if v.get('logical_name') == reference), reference)
        if source not in remapped or not isinstance(patch, dict) or set(patch) - {
                'time_source', 'clock', 'unit', 'field_metadata', 'coordinate_frame', 'calibration_status'}:
            raise ValueError(f'{reference}: unsupported capture override')
        remapped[source].update(copy.deepcopy(patch))
    for key in ('calibration', 'transforms'):
        if key in capture:
            profile[key] = copy.deepcopy(capture[key])
    return load_profile(profile)


def open_config_dataset(config):
    from edc.data.ir.dataset import Dataset, Episode, open_dataset, _set_episode_backend
    cfg = load_config(config)
    data = cfg['dataset']
    profile = execution_profile(cfg)
    if 'episodes' in data:
        root = Path(data['path'])
        if not root.is_dir() or not isinstance(data['episodes'], list):
            raise ValueError('Explicit episodes require a dataset directory and episode list')
        acceptance = root / 'acceptance.json'
        if acceptance.is_file():
            import json
            if json.loads(acceptance.read_text()).get('conversion_status') == 'failed':
                raise ValueError('Conversion acceptance failed; inspect catalog diagnostics')
        episodes = []
        for item in data['episodes']:
            if (not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']
                    or not isinstance(item.get('path'), str) or not isinstance(item.get('format'), str)
                    or not isinstance(item.get('selector', {}), dict)
                    or not isinstance(item.get('metadata', {}), dict)):
                raise ValueError('Explicit episode requires id, path, format and mapping selector/metadata')
            source = (root / item['path']).resolve()
            if not source.is_relative_to(root) or not source.exists():
                raise ValueError('Explicit episode must exist inside dataset.path')
            episode = Episode(item['id'], source, item['format'], profile,
                copy.deepcopy(item.get('selector', {})), copy.deepcopy(item.get('metadata', {})))
            _set_episode_backend(episode, data.get('backend', 'auto'))
            episodes.append(episode)
        if not episodes or len({e.id for e in episodes}) != len(episodes):
            raise ValueError('Explicit episodes require nonempty unique IDs')
        dataset = Dataset(root, profile, data, episodes)
    else:
        dataset = open_dataset(data['path'], profile=profile, backend=data.get('backend', 'auto'))
    metadata = {'id': data.get('id', dataset.id), 'version': data.get('version', dataset.version),
                'tasks': data.get('tasks', dataset.tasks), 'metadata': data.get('metadata', dataset.metadata)}
    from edc.data.ir.canonical import CanonicalDataset
    return CanonicalDataset(Dataset(dataset.path, profile, metadata, list(dataset.iter_episodes())))


def workflow_config(config):
    cfg = load_config(config)
    workflow = copy.deepcopy(cfg['workflow'])
    workflow.update(schema_version='edc_workflow_v1', dataset={'path': cfg['dataset']['path']},
                    unit_policy=cfg['unit_policy'], config_snapshot=cfg)
    from edc.algorithms.registry import algorithms
    registry = algorithms()
    methods = cfg['algorithms']['methods']
    defaults = cfg['algorithms']['defaults']
    for stage in workflow.get('stages', []):
        if stage.get('algorithms', 'recommended') == 'recommended':
            stage['algorithms'] = [k for k, p in registry.plugins.items() if p.spec.category == stage['kind']
                and not p.spec.example and methods.get(k, {}).get('enabled', True)]
            stage['selection_policy'] = 'runnable'
        else:
            stage.setdefault('selection_policy', defaults['selection'])
        params = stage.setdefault('params', {})
        for ident in stage['algorithms']:
            method = methods.get(ident, {})
            params[ident] = {**method.get('params', {}), **params.get(ident, {})}
            plugin = registry.plugins.get(ident)
            channels = cfg['robot'].get('channels', {})
            imu_only = (not cfg['robot'].get('parts') and bool(channels)
                        and all(c.get('modality') == 'imu' for c in channels.values()))
            parts = cfg['robot'].get('parts', {})
            declared_without_arms = bool(parts) and all(
                p.get('type') and p['type'] != 'arm' for p in parts.values())
            # An explicit base/leg/sensor-only topology does not require arms.
            # Unknown topology and explicitly requested arm scopes still fail.
            if plugin and callable(getattr(plugin, 'instances', None)) and (
                    not (imu_only or declared_without_arms) or 'arms' in method or defaults['arms'] != 'all'):
                params[ident].setdefault('arms', method.get('arms', defaults['arms']))
    return workflow


def recommend_config(config, stage=None):
    from edc.algorithms.recommendations import recommend
    workflow = workflow_config(config)
    chosen = next((s for s in workflow['stages'] if s['id'] == stage), None) if stage else workflow['stages'][0]
    if chosen is None:
        raise ValueError('Unknown workflow stage')
    with open_config_dataset(workflow['config_snapshot']) as dataset:
        result = recommend(dataset, chosen.get('params', {}), workflow['unit_policy'])
    result.update(stage=chosen['id'], config_snapshot=workflow['config_snapshot'],
                  notice='Input compatibility only. Runtime value recommendations are recomputed after the quality gate.')
    return result
