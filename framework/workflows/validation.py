"""Static validation of finite workflows before creating output directories."""
import copy
import re
from pathlib import Path
import yaml
from framework.algorithm_contracts import resolve_parameters
from framework.registry import algorithms


def validate_workflow(config):
    base = Path.cwd()
    if isinstance(config, (str, Path)):
        path = Path(config).resolve()
        base = path.parent
        config = yaml.safe_load(path.read_text())
    cfg = copy.deepcopy(config)
    if not isinstance(cfg, dict) or set(cfg) - {'schema_version', 'id', 'dataset', 'unit_policy', 'stages'}:
        raise ValueError('Invalid workflow configuration keys')
    if cfg.get('schema_version') != 'dqs_workflow_v1':
        raise ValueError('Workflow requires schema_version: dqs_workflow_v1')
    if not isinstance(cfg.get('id'), str) or not re.fullmatch('[a-z][a-z0-9_]*', cfg['id']):
        raise ValueError('Workflow id must use lowercase letters, digits and underscores')
    data = cfg.get('dataset')
    if (not isinstance(data, dict) or set(data) - {'path', 'profile', 'robot', 'backend'} or
            not isinstance(data.get('path'), str)):
        raise ValueError('dataset requires path and optional profile/robot/backend')
    if data.get('profile') is not None and data.get('robot') is not None:
        raise ValueError('Use either profile or robot')
    data['path'] = str((base / data['path']).resolve())
    if isinstance(data.get('profile'), str):
        data['profile'] = str((base / data['profile']).resolve())
    if data.get('backend', 'auto') not in {'auto', 'official', 'compat'}:
        raise ValueError('Invalid dataset backend')
    if cfg.get('unit_policy', 'auto') not in {'auto', 'manual'}:
        raise ValueError('Invalid unit policy')
    stages = cfg.get('stages')
    if not isinstance(stages, list) or not stages:
        raise ValueError('Workflow requires a nonempty finite stage list')
    registry = algorithms()
    lineage = {'source': (False, False)}
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) - {'id', 'kind', 'input', 'algorithms', 'params', 'select'}:
            raise ValueError('Invalid stage keys')
        ident = stage.get('id')
        if not isinstance(ident, str) or not re.fullmatch('[a-z][a-z0-9_]*', ident) or ident in lineage:
            raise ValueError('Stage IDs must be unique and safe')
        parent = stage.get('input')
        if parent not in lineage:
            raise ValueError('Stage input must reference source or an earlier stage; cycles are forbidden')
        kind = stage.get('kind')
        if kind not in {'quality', 'value', 'editing', 'augmentation'}:
            raise ValueError('Unknown stage kind')
        ids = stage.get('algorithms')
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Select unique algorithm IDs for each stage')
        params = stage.get('params', {})
        if not isinstance(params, dict) or any(k not in ids for k in params):
            raise ValueError('Stage params must reference selected algorithms')
        for name in ids:
            if name not in registry.plugins:
                raise ValueError(f'Algorithm unavailable: {name}; install its plugin first')
            plugin = registry.plugins[name]
            if plugin.spec.category != kind:
                raise ValueError(f'{name}: plugin category does not match stage kind')
            resolve_parameters(plugin.spec, params.get(name, {}))
        selection = stage.get('select')
        if selection is not None:
            if not isinstance(selection, dict) or set(selection) - {'all', 'episodes', 'where'}:
                raise ValueError('select supports all, episodes and where')
            if ('all' in selection) == ('episodes' in selection) or ('all' in selection and selection['all'] is not True):
                raise ValueError('Explicitly select all:true or episode IDs')
            if 'episodes' in selection and (not isinstance(selection['episodes'], list) or
                    any(not isinstance(x, str) or not x for x in selection['episodes']) or
                    len(set(selection['episodes'])) != len(selection['episodes'])):
                raise ValueError('select.episodes must contain unique IDs')
            if not isinstance(selection.get('where', []), list):
                raise ValueError('select.where must be a list of predicates')
            for condition in selection.get('where', []):
                if (not isinstance(condition, dict) or set(condition) != {'algorithm', 'path', 'op', 'value'} or
                        condition['op'] not in {'eq', 'ne', 'gt', 'gte', 'lt', 'lte'} or
                        not isinstance(condition['path'], str) or not condition['path'] or
                        not isinstance(condition['algorithm'], str) or not condition['algorithm']):
                    raise ValueError('where requires algorithm/path/op/value predicates')
        quality, valued = lineage[parent]
        if kind == 'value' and not quality:
            raise ValueError('Value stage requires a quality stage for this data version')
        if kind == 'augmentation' and not valued:
            raise ValueError('Augmentation must follow value assessment for this data version')
        if kind in {'editing', 'augmentation'}:
            if selection is None or len(ids) != 1:
                raise ValueError('Transform stages require explicit select and exactly one plugin')
            lineage[ident] = (False, False)
        elif kind == 'quality':
            lineage[ident] = (True, valued)
        else:
            lineage[ident] = (quality, True)
    return {'schema_version': 'dqs_workflow_validation_v1', 'valid': True, 'config': cfg,
            'discovery_errors': registry.discovery_errors}
