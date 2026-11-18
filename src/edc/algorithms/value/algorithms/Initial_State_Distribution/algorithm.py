"""Dataset plugin: EDC frames -> SAM3 -> unchanged native distribution metrics."""
from contextlib import closing
import hashlib
from importlib import metadata, util
from importlib.resources import files
import json
from pathlib import Path

from edc.algorithms.registry import AlgorithmSpec
from edc.algorithms.channel_names import resolve_channel_parameters
from edc.reporting.results import write_json

from .metrics import normalize_config, compute_distribution_summary


def _checkpoint(params):
    path = Path(params['checkpoint']).expanduser().resolve()
    if not path.is_file():
        raise ImportError(f'SAM3 checkpoint not found: {path}')
    return path


def _backend(config, params):
    # SAM3, Torch, Torchvision and PIL must never load during plugin discovery.
    from .extractor import Sam3InitialStateExtractor
    return Sam3InitialStateExtractor(config, checkpoint=str(_checkpoint(params)), device=params['device'])


def _missing_dependencies():
    return [name for name in ('sam3', 'torch', 'torchvision', 'PIL') if util.find_spec(name) is None]


def _heatmaps(summary, workdir):
    """Render native probabilities without rescaling or changing the metric."""
    import numpy as np
    from PIL import Image
    paths = {}
    for name, category in summary['categories'].items():
        probabilities = np.asarray(category['heatmap'])
        intensity = np.rint(np.clip(probabilities, 0, 1) * 255).astype('uint8')
        key = hashlib.sha256(name.encode()).hexdigest()[:20]
        filename = f'{key}-probability.png'
        Image.fromarray(intensity).resize((512, 512), Image.Resampling.NEAREST).save(workdir / filename)
        paths[name] = {'path': filename, 'scale': 'linear probability, black=0, white=1'}
    return paths


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _bound_channel(episode, params):
    return resolve_channel_parameters(episode, {'channels': [params['channel']]})['channels'][0]


def _provenance(params):
    checkpoint = _checkpoint(params)
    versions = {}
    for name in ('embodied-data-curation', 'torch', 'torchvision', 'sam3'):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = 'unknown'
    runtime = {}
    if params.get('python'):
        from edc.algorithms.value.runtime import runtime_info, sha256
        info = runtime_info(params['python'], __package__, tuple(versions))
        root = Path(__file__).parent
        sources = {p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob('*'))
                   if p.is_file() and p.suffix in {'.py', '.json', '.yaml'}}
        from edc.algorithms.value import runtime as common
        if info['source_files'] != sources or info['shared_runtime_sha256'] != sha256(common.__file__):
            raise ImportError('Worker must install this exact EDC method/runtime; source fingerprints differ')
        versions = info['versions']
        runtime = {'runtime': info}
    return {**runtime, 'checkpoint': str(checkpoint), 'checkpoint_sha256': _sha256(checkpoint),
            'versions': versions,
            'source_sha256': {p.name: _sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
            'effective_params': params,
            'method_source': json.loads(files(__package__).joinpath('SOURCE.json').read_text())}


class InitialStateDistribution:
    spec = AlgorithmSpec(
        id='Initial_State_Distribution', version='1.1.0',
        name='Initial-state spatial distribution', author='EDC local integration',
        description='Per-category SAM3 detections and image-grid entropy over an explicit dataset.',
        category='value', granularity='dataset', device='optional_cuda',
        parameter_schema={'type': 'object', 'additionalProperties': False,
            'required': ['channel', 'checkpoint', 'comparison_space', 'objects'], 'properties': {
                'python': {'type': 'string', 'description': 'Optional isolated worker interpreter'},
                'timeout_seconds': {'type': 'integer', 'minimum': 1, 'default': 86400},
                'channel': {'type': 'string', 'description': 'Explicit RGB channel or logical name'},
                'checkpoint': {'type': 'string', 'description': 'Existing local SAM3 checkpoint; no download'},
                'comparison_space': {'type': 'string', 'description':
                    'Explicit user-confirmed camera/scene grouping; no automatic cross-camera comparison'},
                'spatial': {'type': 'object', 'description': 'Optional calibrated per-episode registered depth inputs'},
                'device': {'type': 'string', 'enum': ['cuda', 'cpu'], 'default': 'cuda'},
                'frame_index': {'type': 'integer', 'minimum': 0, 'default': 0},
                'grid_size': {'type': 'integer', 'minimum': 2, 'maximum': 256, 'default': 8},
                'nms_iou': {'type': 'number', 'minimum': 0, 'maximum': 1, 'default': .7},
                'objects': {'type': 'array', 'minItems': 1, 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['name', 'prompt'],
                    'properties': {'name': {'type': 'string'}, 'prompt': {'type': 'string'},
                        'confidence_threshold': {'type': 'number', 'minimum': 0, 'maximum': 1}}}}}},
        episode_requirements=({'id': 'rgb', 'channel_param': 'channel', 'modality': 'rgb',
                               'frames_required': True, 'min_samples': 1},),
        input_descriptions=('One explicit RGB view per episode; comparable camera/scene grouping required.',))

    def preflight_episode(self, episode, params):
        channel = episode.channels[_bound_channel(episode, params)]
        if channel.count <= params['frame_index']:
            return 'missing_input', 'Requested initial frame is outside the declared episode'
        return 'runnable', ''

    def preflight_dataset(self, dataset, params):
        try:
            normalize_config(params)
            if not params['comparison_space'].strip() or not params['channel'].strip():
                return 'missing_input', 'Explicit channel and comparison_space are required'
        except (KeyError, ValueError, TypeError) as exc:
            return 'missing_input', str(exc)
        try:
            _checkpoint(params)
        except ImportError as exc:
            return 'missing_dependency', str(exc)
        # Metadata-only checks; do not import a GPU library or initialize a model.
        if params.get('python'):
            from edc.algorithms.value.runtime import check_runtime
            try:
                check_runtime(params, __file__, ['sam3', 'torch', 'torchvision', 'PIL'])
            except ImportError as exc:
                return 'missing_dependency', str(exc)
            missing = []
        else:
            missing = _missing_dependencies()
        if missing:
            return 'missing_dependency', 'Install inference dependencies: ' + ', '.join(missing)
        if not list(dataset.iter_episodes()):
            return 'missing_input', 'No episodes available'
        return 'runnable', ''

    def run(self, context, params):
        config = normalize_config({**params, 'source': params['channel']})
        workdir = Path(context.workdir).resolve()
        write_json(workdir / 'config.json', config)
        spatial = params.get('spatial')
        if spatial:
            from .spatial import validate_inputs
            validate_inputs(context.dataset, params)
        provenance = _provenance(params)
        if spatial:
            from edc.algorithms.value.runtime import fingerprint
            provenance['spatial_inputs'] = {key: fingerprint(item['path']) for key,item in spatial['episodes'].items()}
            provenance['depth_source'] = spatial['source']
            if spatial['source']=='monocular_estimate':
                provenance['depth_model'] = fingerprint(spatial['model'])
        write_json(workdir / 'provenance.json', provenance)
        prepared = _prepare_frames(context, params, workdir)
        if params.get('python'):
            from edc.algorithms.value.runtime import run_worker
            rows = list(prepared)
            write_json(workdir / 'input-mapping.json', {'dataset': context.dataset.id, 'episodes': rows})
            return run_worker(context, params, __package__, {
                'dataset_id': context.dataset.id, 'rows': rows, 'provenance': provenance})
        return evaluate_prepared(prepared, config, params, context.dataset.id, workdir, provenance)


def _prepare_frames(context, params, workdir):
    from PIL import Image
    for episode in context.dataset.iter_episodes():
        row = {'episode': episode.id, 'source': str(episode.path),
               'selector': episode.selector, 'frame_index': params['frame_index']}
        try:
            with episode.opened():
                name = _bound_channel(episode, params)
                channel = episode.channels[name]
                row.update(channel=name, source_mapping=channel.source_mapping,
                           time_source=channel.time_source, clock=channel.clock)
                with closing(episode.iter_frames(name)) as frames:
                    frame = next((frame for index, frame in enumerate(frames)
                                  if index == params['frame_index']), None)
                if frame is None:
                    raise ValueError('Requested initial frame could not be decoded')
                image = Image.fromarray(frame['image']).convert('RGB')
                row['frame_evidence'] = {key: frame.get(key) for key in
                    ('index', 'source_index', 'source_time', 'playback_time', 'container_time')}
                key = hashlib.sha256(episode.id.encode()).hexdigest()[:20]
                row['image_evidence'] = f'{key}-initial.png'
                image.save(workdir / row['image_evidence'])
        except Exception as exc:
            row['extraction_error'] = f'{type(exc).__name__}: {exc}'
        yield row


def evaluate_prepared(prepared, config, params, dataset_id, workdir, provenance):
    """Same native computation for local and isolated execution, using lossless RGB."""
    from PIL import Image
    spatial = params.get('spatial')
    extractor = _backend(config, params)
    extractor.retain_masks = bool(spatial)
    rows, errors, spatial_errors = [], [], []
    for row in prepared:
        try:
            if 'extraction_error' in row:
                raise ValueError(row['extraction_error'])
            with Image.open(workdir / row['image_evidence']) as image:
                row.update(extractor.extract_image(image.convert('RGB')))
            if spatial:
                from .spatial import describe_scene
                try:
                    key = hashlib.sha256(row['episode'].encode()).hexdigest()[:20]
                    row['spatial_state'] = describe_scene(row, spatial, row['episode'], workdir, key)
                except Exception as exc:
                    spatial_errors.append({'episode_id': row['episode'], 'error': str(exc)})
                    row['spatial_error'] = str(exc)
                finally:
                    for category in row['categories'].values():
                        for instance in category['instances']: instance.pop('_mask', None)
        except Exception as exc:
            message = row.get('extraction_error', f'{type(exc).__name__}: {exc}')
            row.update(categories={}, extraction_error=message)
            errors.append({'episode_id': row['episode'], 'error': message})
        rows.append(row)
    detections = {'schema_version': 1, 'dataset': dataset_id, 'source_name': 'edc_rgb_frame',
                  'frame_index': params['frame_index'], 'source_count': len(rows),
                  'extraction_error_count': len(errors), 'extraction_errors': errors, 'episodes': rows}
    summary = compute_distribution_summary(detections, config)
    heatmaps = _heatmaps(summary, workdir)
    write_json(workdir / 'initial_state_detections.json', detections)
    write_json(workdir / 'distribution_summary.json', summary)
    write_json(workdir / 'domain-errors.json', errors)
    write_json(workdir / 'spatial-errors.json', spatial_errors)
    if spatial_errors:
        raise RuntimeError('Spatial assessment incomplete; 2D evidence retained; see spatial-errors.json')
    if errors:
        raise RuntimeError(f'{len(errors)}/{len(rows)} episode extractions failed; see domain-errors.json')
    return {'mode': 'infer', 'comparison_space': params['comparison_space'],
            'distribution': summary, 'per_episode': rows,
            'coverage': {'attempted': len(rows), 'completed': len(rows), 'errors': 0},
            'provenance': provenance, 'heatmaps': heatmaps, 'assessment_complete': True}


plugin = InitialStateDistribution()
