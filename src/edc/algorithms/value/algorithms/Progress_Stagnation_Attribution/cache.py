"""Finalize run-owned image evidence after all requested consumers return."""
import json
from pathlib import Path

from edc.reporting.results import write_json


def finalize_cache(work, manifest, result, *, keep=False):
    work = Path(work).resolve()
    cache_root = work / 'input'
    entries, references = [], {}
    for role, view in manifest['views'].items():
        for index, item in view.get('cached_images', {}).items():
            path = Path(item['path'])
            # Only files created by EDC's cache_frames in this run may be removed.
            if path.is_symlink() or not path.resolve().is_relative_to(cache_root):
                raise ValueError('Image cache ownership cannot be established')
            ref = f'edc-frame:{role}:{index}'
            item.update(frame_ref=ref, cache_state='retained')
            references[str(path)] = ref
            entries.append((path, item))

    def rewrite(value):
        if isinstance(value, str):
            return references.get(value, value)
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        return value

    status = {'status': 'retained', 'images': len(entries), 'errors': [],
              'references': 'edc-frame:<view>:<source-index> resolves through input-mapping.json'}
    if not keep:
        # Persist resolvable references before deleting any images. Model text
        # and numeric observations are unchanged; only image locations change.
        for file in work.rglob('*.json'):
            if file.name == 'input-mapping.json':
                continue
            value = json.loads(file.read_text())
            updated = rewrite(value)
            if updated != value:
                write_json(file, updated)
        result = rewrite(result)
        write_json(work / 'input-mapping.json', manifest)
        for path, item in entries:
            try:
                path.unlink(missing_ok=True)
                item.update(path=None, cache_state='cleaned')
            except OSError as exc:
                status['errors'].append({'frame_ref': item['frame_ref'], 'error': str(exc)})
        status['status'] = 'partial_cleanup' if status['errors'] else 'cleaned'
    result['frame_cache'] = status
    manifest['cache'] = status
    write_json(work / 'input-mapping.json', manifest)
    write_json(work / 'native-result.json', result)
    return result
