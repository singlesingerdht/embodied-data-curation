"""Native outputs remain distinct from quality judgments in both HTML locations."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote

import pytest

from edc.reporting.run import write_run_report


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        self.urls.extend(v for k, v in attrs if k in {'src', 'href'})


def setup_run(tmp_path, reports, *, status='completed', scope='episode', mixed=False):
    output = tmp_path / 'output'
    root = output / '_runs/test'
    folder = root / 'test__value'
    artifacts = folder / 'artifacts' / scope
    artifacts.mkdir(parents=True)
    for name in ('native-result.json', 'provenance.json', 'progress_chart.png', 'cup-probability.png', 'review.mp4'):
        (artifacts / name).write_text('{}')
    spec = {'id': 'test__value', 'version': '1', 'category': 'value'}
    metadata = {'schema_version': 'edc_run_v1', 'run_id': 'test',
                'algorithms': [spec] + ([{'id': 'test__quality', 'category': 'quality'}] if mixed else []),
                'dataset': {'source': '/synthetic', 'episodes': [{'id': 'ep', 'source': '/synthetic/ep'}]},
                'profile': {'id': 'synthetic'}}
    (root / 'run.json').write_text(json.dumps(metadata))
    record = {'schema_version': 'edc_record_v1', 'algorithm': {'id': 'test__value', 'version': '1'},
              'object': {'type': scope, 'id': 'test-dataset' if scope == 'dataset' else 'ep'},
              'status': status, 'result': {'reports': reports} if status == 'completed' else None,
              'reason': 'model failed <script>alert(1)</script>' if status != 'completed' else '',
              'artifacts': [p.relative_to(output).as_posix() for p in artifacts.iterdir()]}
    records_file = folder / 'records.jsonl'
    records_file.write_text(json.dumps(record) + '\n')
    return output, root, record, records_file


def render(output, root, *, mixed=False):
    quality = [{'episode': 'ep', 'decision': 'keep', 'results': []}] if mixed else []
    path = write_run_report(output, root, quality, seconds=1, episode_seconds={'ep': 1})
    for page in (output / path, output / 'report.html'):
        text = page.read_text()
        parser = Links(); parser.feed(text)
        assert parser.urls
        for url in parser.urls:
            if not url.startswith('#'):
                assert (page.parent / unquote(url)).is_file(), url
                (page.parent / unquote(url)).resolve().relative_to(output.resolve())
        assert '<script>' not in text
    return (output / 'report.html').read_text()


@pytest.mark.parametrize('mixed', [False, True])
def test_dataset_distribution_both_locations_and_original_records_unchanged(tmp_path, mixed):
    native = {'assessment_complete': True, 'distribution': {
        'score': 25.5, 'total_episodes': 2, 'grid_size': [8, 8],
        'categories': {'cup<script>': {'score': 25.5, 'entropy': 1.1, 'normalized_entropy': .51,
            'valid_episode_ratio': .5, 'valid_episodes': 1, 'total_episodes': 2, 'total_instances': 3}}}}
    output, root, _, records = setup_run(tmp_path, [native], scope='dataset', mixed=mixed)
    before = records.read_bytes()
    text = render(output, root, mixed=mixed)
    assert '处理完成；评估完成' in text and '未设判定' in text
    assert '数据集：test-dataset' in text and '25.5' in text
    assert 'cup-probability.png' not in text and '<img' not in text and '<video' not in text
    assert '原生结果预览' not in text and '完整结果' in text
    assert '逐条一览' in text and '逐条详情' in text
    assert ('质检报告' in text) is mixed
    assert records.read_bytes() == before
    assert not list(root.rglob('*.mp4'))[0].stat().st_size > 2


def test_incomplete_progress_summarizes_reason_without_expanding_candidates(tmp_path):
    native = {'assessment_complete': False, 'components': {
        'visual': {'status': 'completed'}, 'physical': {'status': 'incomplete',
            'limitations': {'right': ['load unit unknown']}}}, 'evaluation': {'subtask_results': [
                {'step': 1, 'description': 'pick', 'start_frame': 5, 'end_frame': 20,
                 'source_start_time_sec': 10.2, 'source_end_time_sec': 12.9, 'subtask_progress': .8}]},
        'attribution': {'candidates': [{'candidate_id': 'E1', 'kind': 'regression',
            'start_frame': 12, 'end_frame': 14, 'source_start_time_sec': 11.5,
            'status': 'incomplete', 'limitations': ['missing calibration'],
            'conclusions': [{'arm': 'right', 'severity': -1, 'primary_label': 'false_empty_grasp'}]}]}}
    output, root, _, records = setup_run(tmp_path, [native])
    before = records.read_bytes()
    text = render(output, root)
    assert '处理完成；评估未完成' in text
    for expected in ['子任务 1 / 1', '物理归因', 'load unit unknown', '完整结果']:
        assert expected in text
    assert 'E1' not in text and 'false_empty_grasp' not in text and '建议保留' not in text
    assert records.read_bytes() == before


@pytest.mark.parametrize('status', ['processing_error', 'missing_input', 'missing_dependency'])
def test_failure_links_existing_evidence_without_upgrading_result(tmp_path, status):
    output, root, _, _ = setup_run(tmp_path, [], status=status)
    text = render(output, root)
    assert '评估未完成' in text and '完整结果' in text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in text
    assert '；评估完成' not in text


def test_manifest_links_cannot_escape_and_missing_evidence_is_visible(tmp_path):
    output, root, record, records = setup_run(tmp_path, [{'assessment_complete': True}])
    folder = root / 'test__value/artifacts/episode'
    outside = tmp_path / 'secret.png'; outside.write_text('secret')
    (folder / 'escape-probability.png').symlink_to(outside)
    valid = folder / 'literal # question?.json'; valid.write_text('{}')
    record['artifacts'] += [valid.relative_to(output).as_posix(),
        (folder / 'escape-probability.png').relative_to(output).as_posix(),
        '../secret.png', 'https://example.org/remote.png', '/etc/passwd',
        'javascript:alert(1)', '_runs/test/test__value/artifacts/missing.png']
    records.write_text(json.dumps(record) + '\n')
    text = render(output, root)
    assert '证据文件不可用' in text
    assert 'literal%20%23%20question%3F.json' not in text
    assert 'src="https:' not in text and 'href="javascript:' not in text


@pytest.mark.parametrize('scope_complete', [False, True])
def test_missing_completion_declaration_and_withheld_scope_do_not_claim_complete(tmp_path, scope_complete):
    output, root, record, records = setup_run(tmp_path, [{'score': 3}])
    record['scope_complete'] = scope_complete
    records.write_text(json.dumps(record) + '\n')
    text = render(output, root)
    assert ('评估未声明' if scope_complete else '评估未完成') in text


@pytest.mark.parametrize('verdict,expected', [('PASS', '通过'), ('FAIL', '未通过'), ('NEEDS_REVIEW', '需复核')])
def test_method_authored_verdict_and_reason_are_shown_without_new_thresholds(tmp_path, verdict, expected):
    output, root, _, _ = setup_run(tmp_path, [{'assessment_complete': True, 'score': 42,
        'verdict': verdict, 'reason': 'native rule <threshold>'}])
    text = render(output, root)
    assert f'>{expected}</span>' in text and '原生分数：42' in text
    assert 'native rule &lt;threshold&gt;' in text


def test_edc_cli_value_report_catalog_and_no_html(tmp_path, monkeypatch, capsys):
    from edc.algorithms.registry import AlgorithmSpec, Registry
    from edc.execution.cli import main

    class Plugin:
        spec = AlgorithmSpec('test__native', '1', 'native', 'test', 'synthetic', category='value')

        def run(self, context, params):
            context.write_result('provenance.json', {'synthetic': True})
            return {'assessment_complete': True, 'score': 12}

    registry = Registry('edc.algorithms'); registry.register(Plugin())
    monkeypatch.setattr('edc.execution.runner.algorithms', lambda: registry)
    source = tmp_path / 'source'; source.mkdir()
    data = source / 'trajectory.json'; data.write_text(json.dumps({'data': [{'timestamp': 1, 'joint_pos': [1]}]}))
    before = hashlib.sha256(data.read_bytes()).hexdigest()
    for no_html in (False, True):
        output = tmp_path / str(no_html)
        assert main(['run', str(source), '--algorithm', 'test__native', '--output', str(output)] +
                    (['--no-html'] if no_html else [])) == 0
        capsys.readouterr()
        entry, = json.loads((output / 'catalog.json').read_text())['runs']
        assert ('report' in entry) is not no_html
        assert (output / 'report.html').is_file() is not no_html
        record = json.loads((output / entry['records'][0]).read_text())
        assert record['result']['reports'][0]['score'] == 12
    assert hashlib.sha256(data.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('envelope_scope', [False, True])
@pytest.mark.parametrize('verdict,label', [('PASS', '通过'), ('NEEDS_REVIEW', '需复核'), ('FAIL', '未通过')])
def test_fragment_verdict_has_explicit_scope_and_never_completes_episode(tmp_path, envelope_scope, verdict, label):
    from edc.execution.completeness import record_complete
    output, root, record, records = setup_run(tmp_path, [{'assessment_complete': True,
        'verdict': verdict, 'assessment_scope': {'whole_episode': False,
        'original_frame_range': [332, 692], 'source_frame_count': 5899}}])
    record['scope_complete'] = envelope_scope  # Includes historical default True.
    records.write_text(json.dumps(record) + '\n'); before = records.read_bytes()
    assert not record_complete(record)
    text = render(output, root)
    assert '片段评估完成；整轨迹未完成' in text
    assert f'片段：{label}' in text and '[332, 692)' in text
    assert records.read_bytes() == before


@pytest.mark.parametrize('scope', [None, [], {'whole_episode': True},
    {'whole_episode': True, 'original_frame_range': [2, 4], 'source_frame_count': 10}])
def test_invalid_scope_does_not_release_or_break_the_report(tmp_path, scope):
    from edc.execution.completeness import record_complete
    output, root, record, _ = setup_run(tmp_path, [
        {'assessment_complete': True, 'verdict': 'PASS', 'assessment_scope': scope}])
    assert not record_complete(record)
    assert '评估未完成' in render(output, root)
