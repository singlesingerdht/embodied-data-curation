"""Runnable output-contract example; not a calibrated robot safety method."""
import numpy as np
from edc import AlgorithmSpec, Item, QualityAlgorithm


class JointStep(QualityAlgorithm):
    spec = AlgorithmSpec(
        id='team_example__joint_step', version='0.1.0',
        name='Joint step example', author='Example team',
        description='Adjacent left-arm feedback changes; demonstrates the quality API.',
        presets=('quality_cpu',), example=True,
        input_requirements=({'channel': 'joint/arm/left/feedback',
                             'field': 'position', 'unit': 'rad'},),
        parameter_schema={'type': 'object', 'additionalProperties': False,
                          'properties': {'review_rad': {'type': 'number',
                                         'exclusiveMinimum': 0, 'default': 0.2}}})

    def check(self, episode, params):
        name = 'joint/arm/left/feedback'
        # The executor provides the SI view and resolves the declared logical name.
        angles = np.asarray(episode.read_signal(name, 'position'), dtype=float)
        if len(angles) < 2:
            return [Item(name, status='not_applicable', reason='At least two rows are required.')]
        if not np.isfinite(angles).all():
            return [Item(name, verdict='EXCLUDE', reason='Nonfinite joint positions.')]
        step = np.abs(np.diff(angles, axis=0)).reshape(len(angles) - 1, -1).max(axis=1)
        threshold = params.get('review_rad', 0.2)
        rows = np.flatnonzero(step > threshold) + 1
        return [Item(name, verdict='REVIEW' if rows.size else 'PASS',
                     reason=f'{len(rows)} adjacent steps exceed {threshold:g} rad.',
                     metrics={'max_step_rad': float(step.max()), 'threshold_rad': threshold},
                     evidence=[{'row': int(row)} for row in rows[:20]])]


plugin = JointStep()
