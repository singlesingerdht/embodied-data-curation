"""Reproduce formal gates and optional, explicitly assumed-clock MCAP comparison."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import numpy as np

from edc import open_dataset, run
from edc.data.ir.units import SIDataset
from edc.algorithms.quality.algorithms.Action_Smoothness.run_check import assess_arrays


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mcap', type=Path)
    parser.add_argument('--robot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--experimental-common-clock', action='store_true',
                        help='Independent assumed-clock comparison, never formal synchronization evidence')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    from edc.robots.profiles import load_profile
    profile = load_profile(args.robot)
    before = {'mcap': digest(args.mcap), 'robot': digest(args.robot)}
    source = '/right_arm/end_pose'; reference = '/camera_head_front/color/image_raw/compressed'
    params = {'channel': source, 'arms': ['right'], 'timeline': {'mode': 'reference_channel',
        'reference_channel': reference, 'time_source': 'acquisition', 'resample': 'linear',
        'clock_policy': 'require_common_or_explicit_mapping'},
        'expected_rate_hz': 30, 'window_length': 11, 'vel_threshold_factor': .1, 'jerk_threshold_m_s3': 15}
    summary = {'schema_version': 'edc_smoothness_timeline_validation_v1',
               'source': str(args.mcap), 'source_hashes': before, 'runs': {}}
    def execute(label, effective_profile, effective_params, experimental=False):
        output = args.output / label
        with open_dataset(args.mcap, profile=effective_profile) as ds:
            result = run(ds, algorithm_ids=['Action_Smoothness'],
                         params={'Action_Smoothness': effective_params}, output=output)
        records = [json.loads(line) for line in next((output/'_runs').glob(
            '*/Action_Smoothness/records.jsonl')).read_text().splitlines()]
        assert all(r['status'] == 'completed' for r in records), records
        report = records[0]['result']['reports'][0]
        raw = report.get('raw', {}).get('jerk', {})
        summary['runs'][label] = {'experimental_assumed_clock': experimental,
            'status_counts': result['status_counts'], 'verdict': report['verdict'],
            'score': report['score'], 'assessment_complete': report['assessment_complete'],
            'jerk_rms_m_s3': raw.get('score'), 'jerk_details': raw.get('details'),
            'timeline': report['timeline'], 'output': str(output)}
        return report
    formal = execute('formal_reference', profile, params)
    assert formal['verdict'] == 'NEEDS_REVIEW' and formal['score'] is None
    native_params = {**params, 'timeline': {'mode': 'native', 'time_source': 'acquisition'}}
    native = execute('native_diagnostic', profile, native_params)
    assert native['verdict'] == 'NEEDS_REVIEW' and not native['timeline']['calibration_rate_compatible']
    if args.experimental_common_clock:
        assumed = copy.deepcopy(profile)
        for name in (source, reference):
            assumed['channels'][name]['clock'] = 'EXPERIMENT_ONLY_UNCONFIRMED_COMMON_CLOCK'
        assumed['validation_notice'] = 'Assumption only; common acquisition clock has NOT been verified'
        (args.output/'experimental-profile.json').write_text(json.dumps(assumed, ensure_ascii=False, indent=2))
        execute('experimental_reference', assumed, params, experimental=True)
        with open_dataset(args.mcap, profile=profile) as ds:
            with next(SIDataset(ds).iter_episodes()).opened() as ep:
                ts = np.asarray(ep.channels[source].timestamps)
                xyz = ep.read_signal(source, 'position')
                target = ts[0] + np.arange(int(np.floor((ts[-1]-ts[0])*30))+1)/30
                values = np.column_stack([np.interp(target, ts, xyz[:, i]) for i in range(3)])
                report = assess_arrays(values, target)
                summary['runs']['uniform_30hz_diagnostic'] = {
                    'experimental_resampling': True, 'proves_acquisition_sync': False,
                    'frames': len(target), 'jerk_rms_m_s3': report['raw']['jerk']['score'],
                    'notice': 'Uniform synthetic grid for comparison only; not reference_channel mode'}
    summary['source_hashes_unchanged'] = before == {'mcap': digest(args.mcap), 'robot': digest(args.robot)}
    assert summary['source_hashes_unchanged']
    (args.output/'validation.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({k: {x: v.get(x) for x in ('verdict', 'jerk_rms_m_s3', 'assessment_complete')}
                      for k, v in summary['runs'].items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
