"""EDC episode adapter for task-conditioned first-frame placement."""
from contextlib import closing
import hashlib
from importlib.metadata import version
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from edc.algorithms.channel_names import resolve_channel
from edc.algorithms.registry import AlgorithmSpec
from edc.algorithms.value.runtime import sha256


def request_for(episode, params, dataset_id, channel):
    from .method import PlacementRequest
    # A sample-specific SOP overrides a shared one. An explicitly empty SOP fails validation.
    fields = {key: episode.metadata.get(key, params.get(key)) for key in
              ('task_requirements', 'task_description', 'task_id', 'task_version')
              if key in episode.metadata or key in params}
    return PlacementRequest(**fields, dataset_id=dataset_id, record_id=episode.id, camera_view_id=channel)


def validate_endpoint(params):
    url = urlsplit(params['base_url'])
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment):
        raise ValueError('base_url must be an HTTP(S) endpoint without credentials, query or fragment')
    try:
        local = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        local = url.hostname.lower() == 'localhost'
    if not local and not params.get('allow_remote_upload'):
        raise ValueError('Sending a frame to this endpoint requires allow_remote_upload: true')
    if not params['model'].strip():
        raise ValueError('Select the model served by the configured endpoint')


class InitialPlacement:
    spec = AlgorithmSpec(
        id='Initial_Placement_Assessment', version='1.1.0', author='kcon0305/placement-assessment, EDC',
        name='Initial placement assessment', category='value',
        description='Check the first RGB frame against explicit initial placement requirements.',
        data_requirements=({'id': 'initial_rgb', 'channel_param': 'channel', 'modality': 'rgb',
                            'frames_required': True, 'min_samples': 1},),
        parameter_schema={'type': 'object', 'additionalProperties': False,
                          'required': ['channel', 'base_url'], 'properties': {
            'channel': {'type': 'string'}, 'model': {'type': 'string', 'default': 'qwen3.8-max'},
            'base_url': {'type': 'string'},
            'api_key_env': {'type': 'string'}, 'allow_remote_upload': {'type': 'boolean', 'default': False},
            'task_requirements': {'type': 'string'}, 'task_description': {'type': 'string'},
            'task_id': {'type': 'string'}, 'task_version': {'type': 'string'},
            'image_max_side': {'type': 'integer', 'minimum': 256, 'maximum': 8192, 'default': 2048},
            'max_tokens': {'type': 'integer', 'minimum': 512, 'maximum': 16384, 'default': 4096},
            'enable_thinking': {'type': 'boolean'},
            'timeout_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 300, 'default': 60}}})

    def preflight(self, episode, params):
        try:
            validate_endpoint(params)
            request_for(episode, params, 'preflight', resolve_channel(episode, params['channel']))
        except ImportError as exc:
            return 'missing_dependency', f'Install the placement extra: {exc}'
        except ValueError as exc:
            return 'missing_input', str(exc)
        if params.get('api_key_env') and not os.environ.get(params['api_key_env']):
            return 'missing_dependency', f"Set the configured API key environment variable {params['api_key_env']}"
        return 'runnable', ''

    def run(self, context, params):
        from PIL import Image
        from .method import assess, PROMPT_VERSION, SYSTEM_PROMPT
        episode = context.episode
        name = resolve_channel(episode, params['channel'])
        channel = episode.channels[name]
        request = request_for(episode, params, context.dataset.id, name)
        validate_endpoint(params)
        source_dir = Path(__file__).parent
        provenance = {'method_source': json.loads((source_dir / 'SOURCE.json').read_text()),
                      'source_files': {p.name: sha256(p) for p in sorted(source_dir.glob('*.py'))},
                      'model': params['model'], 'endpoint': params['base_url'], 'prompt_version': PROMPT_VERSION,
                      'prompt_sha256': hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                      'dependencies': {p: version(p) for p in ('Pillow', 'pydantic', 'httpx')}}
        context.write_result('provenance.json', provenance)
        with closing(episode.iter_frames(name)) as frames:
            frame = next(frames, None)
        if frame is None or frame['index'] != 0:
            raise ValueError('No decodable first frame (index 0)')
        image = Image.fromarray(frame['image']).convert('RGB')
        path = Path(context.workdir) / 'initial_frame.png'
        image.save(path)
        mapping = {'episode_id': episode.id, 'source': str(episode.path), 'selector': episode.selector,
                   'channel': name, 'channel_mapping': channel.source_mapping, 'time_source': channel.time_source,
                   'clock': channel.clock, 'frame': {k: frame.get(k) for k in
                   ('index', 'source_index', 'source_time', 'container_time', 'playback_time')},
                   'request': request.model_dump(), 'input_sha256': sha256(path),
                   'requirements_sha256': hashlib.sha256(request.task_requirements.encode()).hexdigest()}
        context.write_result('input-mapping.json', mapping)
        result = assess(image, request, params, context)
        status = result['placement_status']
        native = {**result, 'request': request.model_dump(), 'input_sha256': mapping['input_sha256'],
                  'requirements_sha256': mapping['requirements_sha256'], 'schema_version': 1, 'kind': 'placement',
                  'assessment_complete': result['requirements_complete'],
                  'verdict': {'correct': 'PASS', 'incorrect': 'FAIL', 'undetermined': 'NEEDS_REVIEW'}[status],
                  'reason': result['summary']}
        context.write_result('placement_report.json', native)
        return native


plugin = InitialPlacement()
