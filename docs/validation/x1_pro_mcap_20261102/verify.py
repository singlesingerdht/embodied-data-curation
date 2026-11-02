"""Read-only acceptance audit of the real Quickstart and supplementary batch.

Run from the checkout with its formats environment; no raw data is distributed.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import numpy as np

from edc import load_config, open_config_dataset


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.paths = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {'href', 'src'} and value:
                link = urlsplit(value)
                if not link.scheme and not link.netloc and link.path:
                    self.paths.append(unquote(link.path))


def run_records(output):
    catalog = json.loads((output/'catalog.json').read_text())
    # Run IDs begin with UTC wall time; select the most recent independent run.
    folder = sorted((output/'_runs').iterdir())[-1]
    records = {p.parent.name: [json.loads(s) for s in p.read_text().splitlines() if s]
               for p in folder.glob('*/records.jsonl')}
    assert len(records) == 9 and sum(map(len, records.values())) == 10
    assert all(r['status'] != 'processing_error' for rows in records.values() for r in rows)
    links = []
    for rows in records.values():
        for r in rows:
            for artifact in r['artifacts']:
                assert (output/artifact).is_file(), artifact
                links.append(artifact)
    html = Links(); html.feed((output/'report.html').read_text())
    for path in html.paths:
        assert (output/path).exists(), path
    return folder, records, len(links), len(html.paths), catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='configs/x1_pro.yaml')
    parser.add_argument('--output', default='results/x1-pro-mcap')
    parser.add_argument('--batch', default='results/x1-pro-batch.json')
    parser.add_argument('--batch-output', help='Absolute per-file --keep directory when auditing from another cwd')
    parser.add_argument('--summary', default='docs/validation/x1_pro_mcap_20261102/real-summary.json')
    args = parser.parse_args()
    config = load_config(args.config); output = Path(args.output).resolve()
    data = config['dataset']; item, = data['episodes']
    source = Path(data['path'])/item['path']
    before = sha256(source)
    assert before == item['metadata']['source_sha256']
    folder, records, artifacts, html_links, catalog = run_records(output)
    expected = set(config['robot']['numeric_check']['physical_fields'])
    numeric, = records['Numeric_Check']
    reports = numeric['result']['reports']
    physical = {f"{r['input_channel']}.{r['input_field']}" for r in reports if r['physical_checks_applied']}
    assert physical == expected and len(physical) == 10 and len(reports) == 44
    assert all(not r['assessment_complete'] for r in reports if not r['in_physical_scope'])
    assert all(not r['physical_checks_applied'] for r in reports if r['input_field'] == 'effort')
    for ident in ['Sync_Check', 'Root_Cause']:
        assert records[ident][0]['status'] == 'missing_input'
    smooth = {}
    for r in records['Action_Smoothness']:
        report, = r['result']['reports']
        assert report['score'] is None and not report['assessment_complete']
        assert report['verdict'] == 'NEEDS_REVIEW'
        evidence = next(output/p for p in r['artifacts'] if p.endswith('/timeline.json'))
        plan = json.loads(evidence.read_text())
        assert plan['source_time_source'] == plan['reference_time_source'] == 'container'
        assert plan['source_clock'] == plan['reference_clock'] and not plan['proves_acquisition_sync']
        assert plan['resampling_params']['max_gap'] == .1 and not plan['extrapolation']
        times = np.asarray(plan['mapped_source_timestamps_s'])
        mapping = plan['source_mapping']['rows']
        left, right = np.asarray(mapping['left_indices']), np.asarray(mapping['right_indices'])
        weight = np.asarray(mapping['right_weights'])
        assert np.max(times[right]-times[left]) <= .1 and np.all((weight >= 0) & (weight <= 1))
        target = np.asarray(plan['target_timestamps_s'])
        np.testing.assert_allclose(target, (1-weight)*times[left]+weight*times[right], rtol=0, atol=5e-7)
        np.testing.assert_array_equal(target, np.asarray(plan['reference_timestamps_s'])[plan['reference_rows']])
        smooth[r['instance_id']] = {'channel': report['input_channel'], 'evaluated_frames': len(target),
            'jerk_rms_diagnostic_m_s3': report['raw']['jerk']['score'],
            'max_observed_bracket_s': float(np.max(times[right]-times[left])),
            'evidence': str(evidence), 'assessment_complete': False}
    assert set(smooth) == {'left', 'right'}
    video = records['Video_Integrity'][0]['result']['reports']
    assert len(video) == 4 and all(r['input_mode'] == 'frames' for r in video)
    workflow_file = sorted((output/'_workflows').glob('*/workflow.json'))[-1]
    workflow = json.loads(workflow_file.read_text())
    assert workflow['delivery_status'] == 'suspended'
    assert workflow['stages'][0]['gate'][item['id']]['decision'] == 'unassessed'
    batch = json.loads(Path(args.batch).read_text()); batch_item, = batch['results']
    assert batch_item['decision'] == 'unassessed' and set(batch_item['algos']) == set(records)
    batch_output = Path(args.batch_output or batch_item['kept']).resolve()
    _, batch_records, batch_artifacts, batch_html_links, _ = run_records(batch_output)
    for ident, rows in records.items():
        other = batch_records[ident]
        assert [(r['status'], r['instance_id']) for r in rows] == [(r['status'], r['instance_id']) for r in other]
        assert [r.get('effective_params') for r in rows] == [r.get('effective_params') for r in other]
    derivatives = {}
    with open_config_dataset(config) as dataset:
        episodes = list(dataset.iter_episodes()); assert len(episodes) == 1
        with episodes[0].opened() as episode:
            assert len(episode.channels) == 32
            for name in ['/left_arm/joint_states', '/right_arm/joint_states',
                         '/master_left_arm/joint_states', '/master_right_arm/joint_states']:
                axis = episode.time_axis(name, time_source='acquisition')
                t = axis.output
                q = episode.read_signal(name, 'position'); v = episode.read_signal(name, 'velocity')
                # A 40 ms central window, using only this channel's own acquisition stamps.
                a = np.clip(np.searchsorted(t, t-.02), 0, len(t)-1)
                b = np.clip(np.searchsorted(t, t+.02), 0, len(t)-1)
                span = t[b]-t[a]; valid = (span >= .02) & (span <= .1)
                dq = (q[b[valid]]-q[a[valid]])/span[valid, None]; vv = v[valid]
                derivatives[name] = {'window_s': .04, 'valid_rows': int(valid.sum()),
                    'derivative_on_recorded_velocity_slope': (np.sum(vv*dq, axis=0)/np.sum(vv*vv, axis=0)).tolist(),
                    'rmse_rad_s': np.sqrt(np.mean((dq-vv)**2, axis=0)).tolist(),
                    'unit_authority': 'Owner-confirmed rad/s; derivative comparison is diagnostic, not a unit inference'}
            assert episode.channels['/master_right_arm/end_pose'].timestamps is None
            assert episode.time_axis('/master_right_arm/end_pose', time_source='container').output.size
    after = sha256(source); assert before == after
    summary = {'baseline_commit': '87c5e6f12ccd3d3b1889c6e7594869dd26db3969',
        'source': str(source), 'bytes': source.stat().st_size, 'sha256_before': before, 'sha256_after': after,
        'episode_id': item['id'], 'channels': 32, 'task_status': 'unconfirmed', 'clock_relationship': 'unknown',
        'output': str(output), 'run_id': folder.name, 'workflow_file': str(workflow_file),
        'workflow_status': workflow['status'], 'delivery_status': workflow['delivery_status'],
        'expected_config_run_exit_code': 1, 'record_count': 10,
        'methods': {k: [{'status': r['status'], 'instance_id': r['instance_id']} for r in rows] for k, rows in records.items()},
        'numeric_fields': len(reports), 'physical_fields': sorted(physical), 'smoothness': smooth,
        'rgb_frames': {r['input_channel']: r['frame_quality']['real_frames'] for r in video},
        'artifact_links_verified': artifacts, 'report_links_verified': html_links,
        'batch': {'decision': batch_item['decision'], 'output': str(batch_output),
                  'artifact_links_verified': batch_artifacts, 'report_links_verified': batch_html_links,
                  'effective_parameters_match': True}, 'velocity_derivative_diagnostic': derivatives}
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'verified': True, 'summary': args.summary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
