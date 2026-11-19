"""Bind externally prepared depth to the actual RGB input before model startup."""
import hashlib
import json
from pathlib import Path

from edc.algorithms.channel_names import resolve_channel
from edc.algorithms.value.runtime import sha256
from edc.reporting.results import write_json


def validate_correspondence(episode, params, manifest, rgb, output):
    """Validate identity, ordered frames and content; retain failed-check evidence.

    The manifest produced by prepare_depth is also the sensor-depth interchange
    contract. Hashes establish correspondence, not geometric calibration accuracy.
    Resource paths in fingerprints are informational so depth files can be moved.
    """
    cfg = params['depth']
    item = cfg['episodes'][episode.id]
    audit = {'status': 'failed', 'episode_id': episode.id, 'validated_frames': 0,
             'evidence': str(item['correspondence_evidence'])}
    try:
        evidence_path = Path(item['correspondence_evidence'])
        payload = evidence_path.read_bytes()
        audit['evidence_sha256'] = hashlib.sha256(payload).hexdigest()
        evidence = json.loads(payload)
        if not isinstance(evidence, dict):
            raise ValueError('Depth correspondence evidence must be a JSON object')
        selected = manifest['selected_reference_indices']
        reference = manifest['views']['faceImg']
        expected = {
            'status': 'completed', 'episode_id': episode.id, 'selector': manifest['selector'],
            'channel': reference['channel'], 'frame_range': [selected[0], selected[-1] + 1],
            'source': cfg['source'], 'unit': cfg['unit'], 'registered_to_rgb': True,
            'registration_id': cfg['registration_id'],
        }
        for key, value in expected.items():
            if key not in evidence or evidence[key] != value:
                raise ValueError(f'Depth correspondence {key} does not match the selected RGB input/configuration')
        if (not isinstance(evidence.get('source_episode'), str) or
                Path(evidence['source_episode']).resolve() != Path(episode.path).resolve()):
            raise ValueError('Depth correspondence source_episode differs from the current episode')
        if resolve_channel(episode, item['rgb_channel']) != reference['channel']:
            raise ValueError('Depth RGB channel differs from the prepared reference view')
        # A matching RGB manifest alone cannot vouch for a replaced depth array.
        depth = evidence.get('depth')
        files = depth.get('files') if isinstance(depth, dict) else None
        digest = sha256(item['path'])
        audit['depth_sha256'] = digest
        if not isinstance(files, dict) or len(files) != 1 or list(files.values()) != [digest]:
            raise ValueError('Depth file SHA-256 differs from its correspondence evidence')
        frames = evidence.get('frames')
        if not isinstance(frames, list) or len(frames) != len(selected) or len(rgb) != len(selected):
            raise ValueError('Depth correspondence must cover every selected RGB frame exactly once')
        for local_index, original_index in enumerate(selected):
            row = frames[local_index]
            source_index = reference['frames'][original_index]['source_index']
            if (not isinstance(row, dict) or row.get('reference_index') != original_index or
                    row.get('source_index') != source_index):
                raise ValueError(f'Depth correspondence frame identity/order differs at original frame {original_index}')
            pixels = rgb[local_index]
            if row.get('rgb_shape') != list(pixels.shape[:2]):
                raise ValueError(f'Depth RGB dimensions differ at original frame {original_index}')
            if row.get('rgb_sha256') != hashlib.sha256(pixels.tobytes()).hexdigest():
                raise ValueError(f'Depth RGB SHA-256 differs at original frame {original_index}')
            audit['validated_frames'] += 1
        audit.update(status='validated', original_frame_range=expected['frame_range'],
                     registration_id=cfg['registration_id'], depth_source=cfg['source'])
    except (ValueError, TypeError, KeyError, OSError) as exc:
        audit['reason'] = str(exc)
        raise ValueError(f'Invalid depth correspondence: {exc}') from exc
    finally:
        write_json(output, audit)
    return audit
