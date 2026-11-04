"""Run reports share batch presentation without rerunning or changing checks."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from edc.algorithms.base import Item, QualityAlgorithm
from edc.algorithms.registry import AlgorithmSpec, Registry
from edc.execution.cli import main
from edc.reporting.batch import page, summarize


class Check(QualityAlgorithm):
    def __init__(self, ident, fail_on=None):
        self.spec = AlgorithmSpec(ident, '1', ident, 'test', 'Synthetic report test')
        self.calls = []
        self.fail_on = fail_on

    def check(self, episode, params):
        self.calls.append(episode.id)
        if episode.id == self.fail_on:
            raise RuntimeError('test decoder failed')
        return [Item('left', verdict='PASS', metrics={'value': 12}, evidence=[{'source_row': 3}]),
                Item('right', verdict='REVIEW', reason='检查位置：第 4 行', metrics={'value': 21})]


@pytest.fixture
def sample(tmp_path, monkeypatch):
    source = tmp_path / 'data'
    source.mkdir()
    for name in ('first', 'second'):
        episode = source / name
        episode.mkdir()
        (episode / 'trajectory.json').write_text(json.dumps({'data': [
            {'timestamp': i / 30, 'joint_pos': [i * .001]} for i in range(100)]}))
    (source / 'dataset.yaml').write_text(yaml.safe_dump({'schema_version': 1,
        'episodes': [{'id': name, 'path': name, 'format': 'episode'} for name in ('first', 'second')]}))
    profile = tmp_path / 'robot.yaml'
    profile.write_text(yaml.safe_dump({'schema_version': 1, 'id': 'report_test', 'version': '1', 'channels': {}}))
    registry = Registry('edc.algorithms')
    for plugin in (Check('test__first'), Check('test__second', fail_on='second')):
        registry.register(plugin)
    monkeypatch.setattr('edc.execution.runner.algorithms', lambda: registry)
    return source, profile, registry


def fingerprint(source):
    return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob('*') if p.is_file()}


def saved(output):
    catalog = json.loads((output / 'catalog.json').read_text())
    entry = catalog['runs'][-1]
    root = output / Path(entry['run']).parent
    quality = json.loads((root / 'quality.json').read_text())
    html = (output / 'report.html').read_text()
    assert html == (output / entry['report']).read_text()
    return entry, quality, html


def test_cli_selected_algorithms_share_batch_findings_and_layout(sample, tmp_path, capsys):
    source, profile, registry = sample
    before = fingerprint(source)
    output = tmp_path / 'run'
    args = ['run', str(source), '--robot', str(profile), '--episode', '0',
            '--algorithm', 'test__second', '--algorithm', 'test__first', '--output', str(output)]
    assert main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    entry, quality, html = saved(output)
    assert entry['run_id'] == summary['run_id']
    assert len(quality) == 1 and quality[0]['episode'] == 'first'
    batch_summary = summarize(output)
    assert batch_summary['decision'] == quality[0]['decision'] == 'review'
    assert list(batch_summary['algos'].values()) == quality[0]['results']
    assert all(p.calls == ['first'] for p in registry.plugins.values())
    assert html.index('<th>test__second</th>') < html.index('<th>test__first</th>')
    assert html.count('<h2>') == 4
    assert html.count('<details') == 3  # Two selected algorithms and one episode.
    assert '检查位置：第 4 行' in html
    assert '没跑完' not in html and '未评估完' not in html
    batch_html = page([{**batch_summary, 'name': 'first', 'source': str(source), 'seconds': 0}],
        {'robot': 'report_test', 'inputs': str(source), 'profile': str(profile),
         'finished': 'test', 'seconds': 0, 'jobs': 1})
    assert html.split('<style>', 1)[1].split('</style>', 1)[0] == batch_html.split('<style>', 1)[1].split('</style>', 1)[0]
    for heading in ('整体结论', '逐条一览', '按算法看问题', '逐条详情'):
        assert heading in html and heading in batch_html
    assert fingerprint(source) == before


def test_failed_episode_keeps_successful_results_and_error_exit(sample, tmp_path, capsys):
    source, profile, registry = sample
    output = tmp_path / 'run'
    assert main(['run', str(source), '--robot', str(profile), '--algorithm', 'test__first',
                 '--algorithm', 'test__second', '--output', str(output)]) == 1
    capsys.readouterr()
    _, quality, html = saved(output)
    assert [r['decision'] for r in quality] == ['review', 'unassessed']
    assert 'first' in html and 'second' in html and 'test decoder failed' in html
    assert '未评估完' in html and '没跑完' in html and '检查位置：第 4 行' in html
    assert all(p.calls == ['first', 'second'] for p in registry.plugins.values())
    for row in quality:
        good = next(r for r in row['results'] if r['algorithm'] == 'test__first')
        assert good['items'][0]['metrics'] == {'value': 12}
        assert good['items'][0]['evidence'] == [{'source_row': 3}]


def test_repeated_run_retains_each_report_and_refreshes_latest(sample, tmp_path, capsys):
    source, profile, _ = sample
    output = tmp_path / 'run'
    args = ['run', str(source), '--robot', str(profile), '--episode', '0', '--output', str(output)]
    assert main(args + ['--algorithm', 'test__first']) == 0
    first_entry, _, first_html = saved(output)
    assert main(args + ['--algorithm', 'test__second']) == 0
    second_entry, _, second_html = saved(output)
    capsys.readouterr()
    assert first_entry['run_id'] != second_entry['run_id']
    assert (output / first_entry['report']).read_text() == first_html
    assert '<th>test__second</th>' in second_html and 'test__first' not in second_html
    assert '<th>test__first</th>' in first_html and 'test__second' not in first_html


def test_actual_builtin_selection_omits_unselected_checks(tmp_path):
    source = tmp_path / 'episode'
    source.mkdir()
    (source / 'trajectory.json').write_text(json.dumps({'data': [
        {'timestamp': i / 30, 'joint_pos': [i * .001]} for i in range(100)]}))
    output = tmp_path / 'run'
    assert main(['run', str(source), '--algorithm', 'Structure_Check', '--output', str(output)]) == 0
    _, quality, html = saved(output)
    assert [r['algorithm'] for r in quality[0]['results']] == ['Structure_Check']
    assert '结构完整性' in html
    assert '数值检查' not in html and '动作平滑度' not in html
    assert html.count('<details') == 2


@pytest.mark.parametrize('algorithm', ['test__first', 'test__second'])
def test_no_html_preserves_quality_records_evidence_and_exit_status(sample, tmp_path, monkeypatch, capsys,
                                                                   algorithm):
    source, profile, _ = sample
    before = fingerprint(source)
    baseline, output = tmp_path / 'with_html', tmp_path / 'without_html'
    args = ['run', str(source), '--robot', str(profile), '--algorithm', algorithm]
    expected_exit = main(args + ['--output', str(baseline)])
    expected_summary = json.loads(capsys.readouterr().out)
    _, expected_quality, _ = saved(baseline)

    def unexpected_render(*args, **kwargs):
        pytest.fail('HTML rendering must be skipped, not generated and deleted')

    monkeypatch.setattr('edc.reporting.run.write_run_report', unexpected_render)
    assert main(args + ['--output', str(output), '--no-html']) == expected_exit
    summary = json.loads(capsys.readouterr().out)
    assert summary['status_counts'] == expected_summary['status_counts']
    entry, = json.loads((output / 'catalog.json').read_text())['runs']
    assert 'report' not in entry
    assert not list(output.rglob('*.html'))
    root = output / Path(entry['run']).parent
    assert json.loads((root / 'quality.json').read_text()) == expected_quality
    for path in entry['records']:
        records = [json.loads(line) for line in (output / path).read_text().splitlines()]
        assert len(records) == 2
        assert all((output / artifact).is_file() for record in records for artifact in record['artifacts'])
    assert fingerprint(source) == before


@pytest.mark.parametrize('keep_details', [False, True])
def test_batch_generates_only_one_html_with_real_child_processes(sample, tmp_path, monkeypatch, keep_details):
    import edc
    from edc.execution import batch

    source, profile, _ = sample
    before = fingerprint(source)
    # Child processes must use the same source tree or installed wheel as this test.
    monkeypatch.setenv('PYTHONPATH', str(Path(edc.__file__).resolve().parents[1]))
    output = tmp_path / 'report.html'
    kept = tmp_path / 'details' if keep_details else None
    result = batch.run_batch([source], profile=profile, output=output, keep=kept, jobs=1, log=lambda _: None)
    assert result['files'] == 2
    assert list(tmp_path.rglob('*.html')) == [output]
    rows = json.loads(output.with_suffix('.json').read_text())['results']
    assert len(rows) == 2 and all(len(row['algos']) == 9 for row in rows)
    if kept:
        for row in rows:
            entry, = json.loads((Path(row['kept']) / 'catalog.json').read_text())['runs']
            assert 'report' not in entry
            assert all((Path(row['kept']) / record).is_file() for record in entry['records'])
    assert fingerprint(source) == before
