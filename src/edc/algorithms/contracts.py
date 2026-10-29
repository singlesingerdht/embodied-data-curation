"""Declarative algorithm inputs and a small dependency-free parameter schema.

This is metadata preflight, never a payload-quality test. Older plugins with
no schema keep their existing parameter handling.
"""
import copy
import math


TYPES = {'object': dict, 'array': (list, tuple), 'string': str,
         'integer': int, 'number': (int, float), 'boolean': bool, 'null': type(None)}


def validate_schema(schema):
    if not isinstance(schema, dict):
        raise ValueError('parameter_schema must be an object')
    if not schema:
        return
    allowed = {'type', 'properties', 'required', 'additionalProperties', 'default', 'enum',
               'minimum', 'maximum', 'exclusiveMinimum', 'minItems', 'items', 'description'}
    if set(schema) - allowed:
        raise ValueError('Unsupported parameter_schema keys: ' + ', '.join(sorted(set(schema) - allowed)))
    kinds = schema.get('type', 'object')
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if not kinds or any(not isinstance(k, str) or k not in TYPES for k in kinds):
        raise ValueError('Invalid parameter_schema type')
    properties = schema.get('properties', {})
    if not isinstance(properties, dict) or any(not isinstance(k, str) for k in properties):
        raise ValueError('parameter_schema properties must be named objects')
    for child in properties.values():
        validate_schema(child)
    required = schema.get('required', [])
    if not isinstance(required, list) or any(k not in properties for k in required):
        raise ValueError('parameter_schema required must reference properties')
    if 'additionalProperties' in schema and type(schema['additionalProperties']) is not bool:
        raise ValueError('parameter_schema additionalProperties must be boolean')
    for key in ('minimum', 'maximum', 'exclusiveMinimum'):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            raise ValueError(f'parameter_schema {key} must be finite numeric')
    if 'minItems' in schema and (type(schema['minItems']) is not int or schema['minItems'] < 0):
        raise ValueError('parameter_schema minItems must be nonnegative integer')
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum']):
        raise ValueError('parameter_schema enum must be nonempty array')
    if 'items' in schema:
        validate_schema(schema['items'])
    if 'default' in schema:
        validate_value(schema['default'], schema, 'default')


def validate_value(value, schema, path):
    kinds = schema.get('type')
    if kinds:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        def matches(k):
            return isinstance(value, TYPES[k]) and not (k in {'number', 'integer'} and isinstance(value, bool))
        if not any(matches(k) for k in kinds):
            raise ValueError(f'{path}: expected {kinds}')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(f'{path}: must be finite')
        for key in ('minimum', 'maximum', 'exclusiveMinimum'):
            if key not in schema:
                continue
            invalid = (value < schema[key] if key == 'minimum' else value > schema[key] if key == 'maximum' else value <= schema[key])
            if invalid:
                raise ValueError(f'{path}: violates {key}')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: expected one of {schema["enum"]}')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if schema.get('additionalProperties', True) is False and set(value) - set(properties):
            raise ValueError(f'{path}: unknown parameters {sorted(set(value) - set(properties))}')
        for key in schema.get('required', []):
            if key not in value:
                raise ValueError(f'{path}.{key}: required')
        for key, item in value.items():
            if key in properties:
                validate_value(item, properties[key], f'{path}.{key}')
    if isinstance(value, (list, tuple)):
        if len(value) < schema.get('minItems', 0):
            raise ValueError(f'{path}: too few items')
        for i, item in enumerate(value):
            if 'items' in schema:
                validate_value(item, schema['items'], f'{path}[{i}]')


def resolve_parameters(spec, supplied=None):
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, dict):
        raise ValueError('Algorithm parameters must be an object')
    result = copy.deepcopy(supplied)
    schema = spec.parameter_schema
    if schema:
        for key, child in schema.get('properties', {}).items():
            if key not in result and 'default' in child:
                result[key] = copy.deepcopy(child['default'])
        validate_value(result, schema, spec.id)
    return result


def validate_requirements(requirements, schema):
    from edc.data.ir.units import si_unit
    allowed = {'id', 'channel', 'channel_param', 'modality', 'field', 'unit', 'source_kind',
               'calibration_status', 'time_sources', 'clock_required', 'min_samples',
               'min_channels', 'when'}
    ids = set()
    if not isinstance(requirements, (tuple, list)):
        raise ValueError('data_requirements must be an array')
    for req in requirements:
        if not isinstance(req, dict) or set(req) - allowed:
            raise ValueError('Invalid data_requirements keys')
        if not isinstance(req.get('id'), str) or not req['id'] or req['id'] in ids:
            raise ValueError('data_requirements require unique ids')
        ids.add(req['id'])
        if ('channel' in req) == ('channel_param' in req):
            raise ValueError('data_requirements need channel or channel_param')
        for key in ('channel', 'channel_param', 'modality', 'field', 'unit', 'source_kind', 'calibration_status'):
            if key in req and (not isinstance(req[key], str) or not req[key]):
                raise ValueError(f'data_requirements {key} must be a nonempty string')
        if 'unit' in req and ('field' not in req or si_unit(req['unit']) != req['unit']):
            raise ValueError('data_requirements unit requires field and SI unit')
        if 'time_sources' in req and (not isinstance(req['time_sources'], (list, tuple)) or not req['time_sources']
                or set(req['time_sources']) - {'acquisition', 'container', 'playback', 'synthetic', 'unknown'}):
            raise ValueError('Invalid data_requirements time_sources')
        if 'min_samples' in req and (type(req['min_samples']) is not int or req['min_samples'] < 1):
            raise ValueError('data_requirements min_samples must be positive integer')
        if 'min_channels' in req and (type(req['min_channels']) is not int or req['min_channels'] < 1):
            raise ValueError('data_requirements min_channels must be positive integer')
        if 'clock_required' in req and type(req['clock_required']) is not bool:
            raise ValueError('data_requirements clock_required must be boolean')
        if 'when' in req and not isinstance(req['when'], dict):
            raise ValueError('data_requirements when must be an object')
        references = list(req.get('when', {})) + ([req['channel_param']] if 'channel_param' in req else [])
        if any(key not in schema.get('properties', {}) for key in references):
            raise ValueError('data_requirements reference undeclared parameter')
        for key, value in req.get('when', {}).items():
            validate_value(value, schema['properties'][key], f'data_requirements.when.{key}')
        if 'channel_param' in req:
            kinds = schema['properties'][req['channel_param']].get('type')
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if not set(kinds) & {'string', 'array'} or set(kinds) - {'string', 'array', 'null'}:
                raise ValueError('data_requirements channel_param must reference a string or string-array parameter')
            if 'array' in kinds:
                items = schema['properties'][req['channel_param']].get('items', {})
                if items.get('type') != 'string':
                    raise ValueError('data_requirements channel_param arrays must contain strings')


def evaluate_requirements(spec, episode, params):
    from edc.data.ir.units import require_si_field, UnitInputError
    rows = []
    for requirement in spec.data_requirements:
        active = all(params.get(k) == v for k, v in requirement.get('when', {}).items())
        raw = requirement.get('channel') or params.get(requirement.get('channel_param'))
        names = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        # An omitted optional collection is resolved by the algorithm's own
        # preflight (for the indicator pool this means all matching channels).
        if isinstance(raw, (list, tuple)) and not raw:
            names = [None]
        for index, requested in enumerate(names):
            row_id = requirement['id'] if not isinstance(raw, (list, tuple)) else f"{requirement['id']}[{index}]"
            row = {'id': row_id, 'requirement': copy.deepcopy(requirement),
                   'channel': requested, 'status': 'not_required', 'reasons': []}
            if isinstance(raw, (list, tuple)):
                row['channel_index'] = index
            if not active:
                rows.append(row)
                continue
            parameter = spec.parameter_schema.get('properties', {}).get(requirement.get('channel_param'), {})
            kinds = parameter.get('type', [])
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if requested is None and 'array' in kinds:
                row['status'] = 'deferred'
                row['reasons'].append('No explicit channel selection; algorithm preflight resolves its default scope')
                rows.append(row)
                continue
            name = requested
            if isinstance(name, str):
                from edc.algorithms.channel_names import resolve_channel
                try:
                    name = resolve_channel(episode, name)
                    row['channel'] = name
                except ValueError:
                    pass
            c = episode.channels.get(name) if isinstance(name, str) else None
            reasons = row['reasons']
            if c is None:
                reasons.append(f'{name or requirement.get("channel_param")}: required channel is unavailable')
            else:
                row['provided'] = {k: getattr(c, k, None) for k in ('modality', 'count', 'time_source', 'clock', 'source_kind')}
                if len(names) < requirement.get('min_channels', 1):
                    reasons.append(f'{requirement.get("channel_param")}: requires >= {requirement["min_channels"]} channels')
                if requirement.get('modality') and c.modality != requirement['modality']:
                    reasons.append(f'{name}: modality must be {requirement["modality"]}')
                if requirement.get('modality') == 'rgb' and not c.image_available:
                    reasons.append(f'{name}: readable RGB frames are required')
                field = requirement.get('field')
                field_map = params.get('field_map', {}) if isinstance(params, dict) else {}
                mapped = field_map.get(name, field_map.get(requested, {})) if isinstance(field_map, dict) else {}
                actual_field = mapped.get(field, field) if isinstance(mapped, dict) and field else field
                if actual_field and (actual_field not in c.fields or not c.value_available):
                    reasons.append(f'{name}.{actual_field}: required numeric field is unavailable')
                elif actual_field:
                    metadata = c.field_spec(actual_field)
                    for key in ('source_kind', 'calibration_status'):
                        if key in requirement and metadata.get(key) != requirement[key]:
                            reasons.append(f'{name}.{actual_field}: {key} must be {requirement[key]}')
                    if 'unit' in requirement:
                        try:
                            require_si_field(episode, name, actual_field, requirement['unit'])
                        except UnitInputError as exc:
                            reasons.append(str(exc))
                if not actual_field:
                    for key in ('source_kind', 'calibration_status'):
                        if key in requirement and getattr(c, key, None) != requirement[key]:
                            reasons.append(f'{name}: {key} must be {requirement[key]}')
                if c.count < requirement.get('min_samples', 1):
                    reasons.append(f'{name}: requires >= {requirement.get("min_samples", 1)} samples')
                if 'time_sources' in requirement and (c.time_source not in requirement['time_sources']
                        or c.timestamps is None or len(c.timestamps) != c.count):
                    reasons.append(f'{name}: requires per-row timestamps from {requirement["time_sources"]}')
                elif 'time_sources' in requirement:
                    import numpy as np
                    if not np.isfinite(c.timestamps).all():
                        reasons.append(f'{name}: timestamps must be finite')
                if requirement.get('clock_required') and not c.clock:
                    reasons.append(f'{name}: explicit clock is required')
            row['status'] = 'missing_input' if reasons else 'satisfied'
            rows.append(row)
    return rows


def bind_parameters(spec, episode, supplied=None):
    """Freeze channel references once for both preflight and execution."""
    from edc.algorithms.channel_names import resolve_channel_parameters, resolve_channel, channel_parameter_bindings
    requested = resolve_parameters(spec, supplied)
    effective = resolve_channel_parameters(episode, requested)
    bindings = channel_parameter_bindings(episode, requested)
    for req in spec.data_requirements:
        key = req.get('channel_param')
        if key and effective.get(key) is not None:
            value = effective[key]
            if isinstance(value, (list, tuple)):
                sources = [resolve_channel(episode, name) for name in value]
                if len(set(sources)) != len(sources):
                    raise ValueError(f'Duplicate {key} selection after resolving logical names')
                effective[key] = sources
                if not any(row.get('parameter', '').startswith(key + '[') for row in bindings):
                    for row in channel_parameter_bindings(episode, {'channels': list(requested[key])}):
                        row['parameter'] = key + row['parameter'][len('channels'):]
                        bindings.append(row)
            else:
                source = resolve_channel(episode, value)
                effective[key] = source
                if not any(row.get('parameter') == key for row in bindings):
                    bindings.append({'parameter': key, 'requested': requested[key], 'channel': source})
    return effective, bindings
