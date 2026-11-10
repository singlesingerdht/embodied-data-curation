"""New runs use EDC contracts; old identities cannot bypass provenance checks."""
import copy
import json
from importlib.metadata import version

import pytest
import yaml

from edc import open_dataset, run
from edc.schemas import CATALOG, DATASET_REFERENCE, RECORD, RUN
from edc.workflows.engine import read_records
from edc.workflows.references import dataset_reference, open_dataset_reference
from edc.workflows.reviews import read_reviews, review


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'source'
    path.mkdir()
    (path / 'trajectory.json').write_text(json.dumps({'data': [
        {'timestamp': i * .1, 'joint_pos': [i * .01]} for i in range(40)]}))
    return path


def test_new_run_identity_and_reference_roundtrip(source, tmp_path):
    original = (source / 'trajectory.json').read_bytes()
    output = tmp_path / 'results'
    with open_dataset(source) as dataset:
        ref = dataset_reference(dataset)
        assert ref['schema_version'] == DATASET_REFERENCE
        with open_dataset_reference(ref) as reopened:
            assert dataset_reference(reopened) == ref
        run(dataset, algorithm_ids=['Numeric_Check'], output=output)
    catalog = json.loads((output / 'catalog.json').read_text())
    assert catalog['schema_version'] == CATALOG
    entry = catalog['runs'][0]
    metadata = json.loads((output / entry['run']).read_text())
    assert metadata['schema_version'] == RUN
    assert metadata['framework_version'] == version('embodied-data-curation')
    records = read_records(output, entry)
    assert records and all(r['schema_version'] == RECORD for r in records)
    assert (source / 'trajectory.json').read_bytes() == original


@pytest.mark.parametrize('schema', ['dqs_dataset_ref_v1', 'edc_dataset_ref_v2', None])
def test_old_or_unknown_reference_is_not_auto_renamed(source, schema):
    with open_dataset(source) as dataset:
        ref = dataset_reference(dataset)
    ref['schema_version'] = schema
    with pytest.raises(ValueError, match='Unsupported dataset reference'):
        open_dataset_reference(ref)


def test_fingerprint_and_source_content_are_still_verified(source):
    with open_dataset(source) as dataset:
        ref = dataset_reference(dataset)
    changed = copy.deepcopy(ref)
    changed['episodes'][0]['id'] = 'different'
    with pytest.raises(ValueError, match='metadata was modified'):
        open_dataset_reference(changed)
    (source / 'trajectory.json').write_text('{}')
    with pytest.raises(ValueError, match='stale'):
        open_dataset_reference(ref)


@pytest.mark.parametrize('schema', ['dqs_catalog_v1', 'edc_catalog_v2', None])
def test_old_catalog_is_not_appended_or_mutated(source, tmp_path, schema):
    output = tmp_path / 'old'
    output.mkdir()
    path = output / 'catalog.json'
    path.write_text(json.dumps({'schema_version': schema, 'runs': []}))
    before = path.read_bytes()
    with open_dataset(source) as dataset, pytest.raises(ValueError, match='catalog'):
        run(dataset, output=output, algorithm_ids=['Numeric_Check'])
    assert path.read_bytes() == before
    assert not (output / '.edc.lock').exists()
    assert not (output / '_runs').exists()


def test_consumers_reject_old_record_inside_new_catalog(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_text(json.dumps({'schema_version': 'dqs_record_v1'}) + '\n')
    entry = {'run_id': 'test', 'records': [path.name]}
    (tmp_path / 'catalog.json').write_text(json.dumps({'schema_version': CATALOG, 'runs': [entry]}))
    with pytest.raises(ValueError, match='Unsupported schema'):
        read_records(tmp_path, entry)
    with pytest.raises(ValueError, match='Unsupported schema'):
        review(tmp_path, run_id='test', algorithm='test', object_id='test',
               action='confirmed', reviewer='test', reason='test', evidence='test')
    assert not (tmp_path / 'reviews.jsonl').exists()


def test_old_review_is_not_reused(tmp_path):
    path = tmp_path / 'reviews.jsonl'
    path.write_text(json.dumps({'schema_version': 'dqs_review_v1'}) + '\n')
    with pytest.raises(ValueError, match='Invalid review'):
        read_reviews(path)


@pytest.mark.parametrize('both', [False, True])
def test_old_source_mapping_cannot_be_silently_ignored(source, both):
    (source / 'dqs-source-mapping.json').write_text('{}')
    if both:
        (source / 'edc-source-mapping.json').write_text('{}')
    with pytest.raises(ValueError, match='Legacy source mapping'):
        open_dataset(source)


def test_edc_source_mapping_retains_episode_identity(tmp_path):
    source = tmp_path / 'dataset'
    source.mkdir()
    for name in ('first', 'second'):
        (source / name).mkdir()
    (source / 'dataset.yaml').write_text(yaml.safe_dump({'schema_version': 1, 'episodes': [
        {'id': name, 'path': name, 'format': 'episode', 'selector': {'episode_index': i}}
        for i, name in enumerate(('first', 'second'))]}))
    mapping = {'schema_version': 'edc_source_mapping_v1', 'episodes': [
        {'episode_index': i, 'id': f'stable-{i}', 'source': f'original-{i}',
         'selector': {'episode_index': i}, 'row_mapping': 'identity'} for i in range(2)]}
    path = source / 'edc-source-mapping.json'
    path.write_text(json.dumps(mapping))
    before = path.read_bytes()
    with open_dataset(source) as dataset:
        episodes = list(dataset.iter_episodes())
        assert [ep.id for ep in episodes] == ['stable-0', 'stable-1']
        assert episodes[1].selector['source_mapping']['source'] == 'original-1'
    assert path.read_bytes() == before
