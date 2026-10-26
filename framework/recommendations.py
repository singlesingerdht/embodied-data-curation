"""Metadata/dependency preflight, not execution or a data quality judgment."""
from Quality_Assessment.adapters.cpu import preflight
from framework.ir.units import SIDataset
from framework.registry import algorithms
from framework.algorithm_contracts import resolve_parameters, evaluate_requirements, bind_parameters


def recommend_episode(episode, plugins, params=None, selected=None):
    params = params or {}
    entries = []
    for ident, plugin in plugins.items():
        if plugin.spec.granularity != 'episode':
            continue
        supplied = params.get(ident, {})
        try:
            effective, resolved_bindings = bind_parameters(plugin.spec, episode, supplied)
            parameter_status = 'valid'
            parameter_error = None
            requirement_checks = evaluate_requirements(plugin.spec, episode, effective)
        except (ValueError, TypeError) as exc:
            effective, parameter_status, parameter_error = dict(supplied), 'invalid', str(exc)
            requirement_checks = []
        status, reason = preflight(plugin, episode, supplied)
        bindings = resolved_bindings if parameter_status == 'valid' else []
        hook = getattr(plugin, 'input_bindings', None)
        if parameter_status == 'valid' and callable(hook):
            try:
                bindings += hook(episode, effective)
            except ValueError as exc:
                status, reason = 'missing_input', str(exc)
        details = {}
        if status == 'runnable':
            hook = getattr(plugin, 'availability', None)
            if callable(hook):
                try:
                    details = hook(episode, effective)
                    if (not isinstance(details, dict) or
                            details.get('availability', 'available') not in {'available', 'partial'}):
                        raise ValueError('availability hook must return available/partial metadata')
                except Exception as exc:
                    status, reason = 'processing_error', f'{type(exc).__name__}: {exc}'
                    details = {}
        availability = details.get('availability', 'available') if status == 'runnable' else 'unavailable'
        entries.append({'algorithm': {'id': ident, 'version': plugin.spec.version},
            'status': status, 'availability': availability, 'reason': reason or details.get('reason', ''),
            'scope': details.get('scope', 'declared_inputs_and_environment'),
            'input_requirements': list(plugin.spec.input_requirements),
            'required_inputs': list(plugin.spec.required_inputs),
            'optional_inputs': list(plugin.spec.optional_inputs),
            'data_requirements': list(plugin.spec.data_requirements),
            'input_descriptions': list(plugin.spec.input_descriptions),
            'requirement_checks': requirement_checks, 'channel_bindings': bindings,
            'parameters': {'definitions': plugin.spec.parameters, 'schema': plugin.spec.parameter_schema,
                           'supplied': supplied, 'effective': effective, 'status': parameter_status,
                           'error': parameter_error},
            'environment_requirements': {'dependencies': list(plugin.spec.dependencies),
                                         'device': plugin.spec.device, 'calibration': plugin.spec.calibration},
            'unit_contract': 'explicit' if plugin.spec.input_requirements or any(
                'unit' in requirement for requirement in plugin.spec.data_requirements) else 'algorithm_preflight_or_capabilities',
            'selected': ident in selected if selected is not None else None,
            'fields': details.get('fields', []), 'inputs': details.get('inputs', []),
            'unavailable_inputs': details.get('unavailable_inputs', [])})
    return {'schema_version': 'dqs_recommendations_v1', 'episode_id': episode.id,
        'unit_policy': episode.unit_policy, 'basis': 'metadata_and_dependency_preflight',
        'quality_judgment': None, 'payload_validated': False,
        'notice': '仅表示当前配置与环境满足声明的前置条件；未执行算法，不表示数据合格或完整物理适配。未声明的输入约束不能据此视为已验证。',
        'recommended_algorithms': [e['algorithm']['id'] for e in entries if e['availability'] == 'available'],
        'partial_algorithms': [e['algorithm']['id'] for e in entries if e['availability'] == 'partial'],
        'unavailable_algorithms': [e['algorithm']['id'] for e in entries if e['availability'] == 'unavailable'],
        'algorithms': entries}


def recommend_dataset(dataset, plugins, params=None, selected=None):
    """Dataset plugins own multi-episode prerequisites; no episode substitution."""
    from importlib import util
    entries = []
    for ident, plugin in plugins.items():
        if plugin.spec.granularity != 'dataset':
            continue
        effective = None
        try:
            effective = resolve_parameters(plugin.spec, (params or {}).get(ident, {}))
        except (ValueError, TypeError) as exc:
            status, reason = 'missing_input', f'Invalid parameters: {exc}'
        else:
            try:
                missing = [dep for dep in plugin.spec.dependencies if util.find_spec(dep) is None]
                status, reason = (('missing_dependency', ', '.join(missing)) if missing else
                                  plugin.preflight_dataset(dataset, effective))
                if status not in {'runnable', 'missing_input', 'needs_calibration', 'missing_dependency', 'processing_error'}:
                    raise ValueError(f'Invalid dataset preflight status: {status}')
            except Exception as exc:
                from framework.ir.units import UnitInputError
                from framework.ir.metadata import IRNotProvided
                status = ('missing_input' if isinstance(exc, (UnitInputError, IRNotProvided)) else
                          'missing_dependency' if isinstance(exc, ImportError) else 'processing_error')
                reason = f'{type(exc).__name__}: {exc}'
            finally:
                for episode in dataset.iter_episodes():
                    episode.close()
        entries.append({'algorithm': {'id': ident, 'version': plugin.spec.version},
            'status': status, 'reason': reason, 'effective_params': effective,
            'availability': 'available' if status == 'runnable' else 'unavailable',
            'selected': ident in selected if selected is not None else None,
            'prerequisite_evaluator': 'plugin.preflight_dataset',
            'data_requirements': list(plugin.spec.data_requirements),
            'input_requirements': list(plugin.spec.input_requirements),
            'granularity': 'dataset', 'quality_judgment': None, 'payload_validated': False})
    return entries


def recommend(dataset, params=None, unit_policy='auto'):
    """List per-episode algorithm availability without running algorithms/models."""
    registry = algorithms()
    if params is not None and (not isinstance(params, dict) or any(
            key not in registry.plugins or not isinstance(value, dict) for key, value in params.items())):
        raise ValueError('params must map registered algorithm IDs to parameter objects')
    result = []
    execution = SIDataset(dataset, unit_policy)
    for episode in execution.iter_episodes():
        try:
            with episode.opened():
                result.append(recommend_episode(episode, registry.plugins, params))
        except Exception as exc:
            result.append({'schema_version': 'dqs_recommendations_v1', 'episode_id': episode.id,
                           'error': f'{type(exc).__name__}: {exc}', 'algorithms': []})
    try:
        datasets = recommend_dataset(execution, registry.plugins, params)
    finally:
        for episode in execution.iter_episodes():
            episode.close()
    return {'schema_version': 'dqs_recommendation_pool_v1', 'episodes': result,
            'dataset_algorithms': datasets, 'discovery_errors': registry.discovery_errors}
