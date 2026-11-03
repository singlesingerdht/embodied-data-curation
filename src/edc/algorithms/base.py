"""The one result format every quality algorithm returns, and the base class for new algorithms.

Each algorithm looks at one episode and returns one Item per thing it checked (an arm, a camera, a field).
An Item that was checked carries one of four verdicts; an Item that could not be checked says why instead.
The episode decision follows from the verdicts alone, the same way for every algorithm.
"""
from dataclasses import dataclass, field, asdict

PASS, REVIEW, SALVAGE, EXCLUDE = 'PASS', 'REVIEW', 'SALVAGE', 'EXCLUDE'
VERDICTS = {PASS: '通过', REVIEW: '复核', SALVAGE: '可救', EXCLUDE: '排除'}
# Why an item has no verdict.
CHECKED, NOT_APPLICABLE, FAILED = 'checked', 'not_applicable', 'failed'
STATUSES = {CHECKED: '已检查', NOT_APPLICABLE: '不适用', FAILED: '没跑完'}
WORST_FIRST = [EXCLUDE, SALVAGE, FAILED, REVIEW, PASS, NOT_APPLICABLE]
DECISIONS = {'exclude': '排除', 'salvage': '可救', 'unassessed': '未评估完', 'review': '复核', 'keep': '通过',
             'failed': '运行失败'}


@dataclass
class Item:
    """One checked thing. `verdict` is set exactly when `status` is checked; anything but PASS says why."""
    target: str                     # what was checked, e.g. '左臂', 'rgb/head', 'joint/arm/left/feedback.position'
    status: str = CHECKED
    verdict: str = None             # PASS / REVIEW / SALVAGE / EXCLUDE
    reason: str = ''                # one readable sentence: what was found, or why it was not checked
    remedy: str = ''                # SALVAGE: how the data can be repaired
    evidence: list = field(default_factory=list)   # e.g. [{'time_s': 12.4, 'duration_s': 0.7}]
    metrics: dict = field(default_factory=dict)    # numbers behind the verdict

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f'Unknown status {self.status}')
        if (self.status == CHECKED) != (self.verdict in VERDICTS):
            raise ValueError('A checked item needs one of PASS/REVIEW/SALVAGE/EXCLUDE; an unchecked one none')
        if self.verdict == SALVAGE and not self.remedy:
            raise ValueError('SALVAGE needs a remedy')
        if self.level != PASS and not str(self.reason or '').strip():
            raise ValueError(f'{self.level} needs a reason')

    @property
    def level(self):
        return self.verdict if self.status == CHECKED else self.status


@dataclass
class AlgorithmResult:
    algorithm: str
    items: list
    note: str = ''                  # short text for the report cell

    @property
    def level(self):
        """Worst item: EXCLUDE, SALVAGE, failed, REVIEW, PASS, not_applicable."""
        levels = [i.level for i in self.items] or [FAILED]
        return min(levels, key=WORST_FIRST.index)

    def to_dict(self):
        return {'algorithm': self.algorithm, 'level': self.level, 'note': self.note,
                'items': [asdict(i) for i in self.items]}

    @classmethod
    def from_dict(cls, data):
        return cls(data['algorithm'], [Item(**i) for i in data['items']], data.get('note', ''))


def decide(results):
    """Episode decision: any EXCLUDE -> exclude; else any SALVAGE -> salvage; else anything that should have
    run but did not -> unassessed; else any REVIEW -> review; else keep. Not applicable never blocks."""
    levels = {r.level for r in results}
    if EXCLUDE in levels:
        return 'exclude'
    if SALVAGE in levels:
        return 'salvage'
    if FAILED in levels or not results:
        return 'unassessed'
    if REVIEW in levels:
        return 'review'
    return 'keep'


class QualityAlgorithm:
    """Base class for a new quality algorithm (developer side).

    Subclass it, set `spec` (an edc.algorithms.registry.AlgorithmSpec; external ids are team__name) and implement
    `check(episode, params) -> list[Item]`. `episode` gives the robot yaml's data by channel name, e.g.
    `episode.read_signal('joint/arm/left/feedback', 'position')` (SI units) or `episode.channels`.
    Register it under the `edc.algorithms` entry point; `edc batch` then runs it next to the built-in ones.
    """
    spec = None

    def check(self, episode, params):
        raise NotImplementedError

    def run(self, context, params):
        items = self.check(context.episode, params)
        if not items or not all(isinstance(i, Item) for i in items):
            raise ValueError(f'{self.spec.id}: check() must return a nonempty list of Item')
        return {'edc_result': AlgorithmResult(self.spec.id, items).to_dict()}
