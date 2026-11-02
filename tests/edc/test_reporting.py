import json
from pathlib import Path
from html.parser import HTMLParser
import pytest
from edc.reporting.results import report, write_json
from edc.reporting.html import record_detail, media_panel


class Document(HTMLParser):
    def __init__(self):
        super().__init__();self.stack=[];self.text=[];self.raw=[];self.tags=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs);self.tags.append((tag,attrs))
        if tag=='details':self.stack.append(attrs.get('class'))
    def handle_endtag(self, tag):
        if tag=='details':self.stack.pop()
    def handle_data(self, data):
        (self.raw if 'raw' in self.stack else self.text).append(data)


def fixture(root, run_id='one', records=None):
    records=records or [
        {'schema_version':'dqs_record_v1','algorithm':{'id':'Structure_Check'},'object':{'type':'episode','id':'ep1'},
         'status':'completed','result':{'reports':[{'score':95.5,'verdict':'ACCEPTABLE',
             'structure':{'passed':False,'camera_pairs':{'orphan_images':['camera']}}}]},'artifacts':[]},
        {'algorithm':{'id':'Numeric_Check'},'object':{'type':'episode','id':'ep1'},'status':'completed',
         'result':{'reports':[{'score':100,'verdict':'ACCEPTABLE','input_channel':'left','input_field':'position','unit':'rad'}]},'artifacts':[]},
        {'algorithm':{'id':'Sync_Check'},'object':{'type':'episode','id':'ep1'},'status':'missing_input',
         'reason':'Missing capabilities: sync','result':None,'artifacts':[]}]
    work=root/run_id;work.mkdir(parents=True)
    (work/'records.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
    write_json(work/'run.json',{'dataset':{'id':'真实样例','source':'local sample'},'profile':{'channels':{'left':{
        'select':{'channel':'state','field':'position','indices':[0,2]}}}}})
    write_json(work/'selection.json',[{'episode_id':'ep1','decision':'unassessed','assessment_complete':False}])
    entry={'run_id':run_id,'records':[f'{run_id}/records.jsonl'],'run':f'{run_id}/run.json','selection':f'{run_id}/selection.json'}
    catalog=json.loads((root/'catalog.json').read_text()) if (root/'catalog.json').exists() else {'schema_version':'dqs_catalog_v1','runs':[]}
    catalog['runs'].append(entry);write_json(root/'catalog.json',catalog)
    return records


def test_report_separates_execution_quality_and_hidden_raw(tmp_path):
    records = fixture(tmp_path)
    profile = json.loads((tmp_path/'one/run.json').read_text())['profile']
    page = ''.join(record_detail(tmp_path, record, profile) for record in records)
    doc=Document();doc.feed(page)
    visible=''.join(doc.text)
    assert '95.5' in visible and '相机未找到配对' in visible
    assert '不能只看评分' in visible
    assert '同一时钟下的独立采集时间轴' in visible
    assert '拆分视图，非新增样本' in visible
    assert 'schema_version' not in visible and 'schema_version' in ''.join(doc.raw)
    assert all('open' not in attrs for tag,attrs in doc.tags if tag=='details' and attrs.get('class')=='raw')


def test_report_keeps_runs_separate_latest_first(tmp_path):
    fixture(tmp_path,'first');fixture(tmp_path,'second')
    page=report(tmp_path).read_text()
    assert '运行 second' in page and '运行 first' not in page
    assert 'class="history"' not in page
    assert len(json.loads((tmp_path/'catalog.json').read_text())['runs']) == 2


def test_report_does_not_turn_missing_or_error_into_pass(tmp_path):
    records=[{'algorithm':{'id':'team__unknown'},'object':{'type':'episode','id':'ep1'},
              'status':'processing_error','reason':'<script>unsafe</script>','result':None,'artifacts':[]}]
    fixture(tmp_path,records=records)
    page=report(tmp_path).read_text()
    assert 'processing_error' in page and '不适用' in page
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert '原始评分' not in page


def test_report_references_are_local_and_raw_results_unchanged(tmp_path):
    fixture(tmp_path)
    source=tmp_path/'one/records.jsonl';before=source.read_bytes()
    report(tmp_path)
    assert source.read_bytes()==before
    records=[json.loads(x) for x in source.read_text().splitlines()]
    records[0]['artifacts']=['../../outside.json']
    source.write_text('\n'.join(json.dumps(r) for r in records))
    with pytest.raises(ValueError,match='escapes'):report(tmp_path)


def test_report_unknown_plugin_retains_nonstandard_result(tmp_path):
    records=[{'algorithm':{'id':'team__plugin'},'object':{'type':'episode','id':'ep1'},'status':'completed',
              'result':{'reports':[{'structure':'custom text','sub_indicators':True,'custom':{'event':'keep evidence'}}]},'artifacts':[]}]
    fixture(tmp_path,records=records)
    assert 'team__plugin' in report(tmp_path).read_text()
    assert 'keep evidence' in (tmp_path/'one/records.jsonl').read_text()


def test_video_subresult_warning_not_hidden_by_top_level_score(tmp_path):
    records=[{'algorithm':{'id':'Video_Integrity'},'object':{'type':'episode','id':'ep1'},'status':'completed',
              'result':{'reports':[{'score':100,'verdict':'ACCEPTABLE','frame_quality':{
                  'verdict':'NEEDS_REVIEW','reasons':['模糊帧占比 3%']}}]},'artifacts':[]}]
    fixture(tmp_path,records=records)
    page=record_detail(tmp_path, records[0], {});doc=Document();doc.feed(page)
    assert '画面质量子项：模糊帧占比 3%' in ''.join(doc.text)
    assert '请勿仅按顶层评分判断' in ''.join(doc.text)


def test_same_template_and_sections_across_robots_with_and_without_media(tmp_path):
    layouts = []
    for index, name in enumerate(('robot_a', 'robot_b')):
        root = tmp_path/name
        fixture(root)
        snapshot = root/'one/run.json'
        value = json.loads(snapshot.read_text())
        value['dataset']['id'] = f'dataset_{index}'
        value['profile']['id'] = name
        write_json(snapshot, value)
        if index:
            write_json(root/'one/presentation.json', {
                'schema_version':'dqs_presentation_v1', 'channels': [{
                    'label':'camera <script>unsafe</script>', 'video':'one/video.mp4',
                    'segments':[{'start_playback_s':4.5, 'label':'定位候选'}],
                    'candidate':{'image':'one/image.jpg', 'frame':135, 'playback_time_s':4.5}}]})
            catalog=json.loads((root/'catalog.json').read_text())
            catalog['presentation']='one/presentation.json'
            write_json(root/'catalog.json',catalog)
        page = report(root).read_text(); doc = Document(); doc.feed(page)
        layouts.append([attrs['data-section'] for _, attrs in doc.tags if 'data-section' in attrs])
        assert 'content="edc_batch_report_v1"' in page
        assert '<script>unsafe' not in page
        for extra in ('框架流程', '字段检查范围', '数据整理工作流', '视频与画面证据', '算法明细与证据'):
            assert extra not in page
    assert layouts[0] == layouts[1] == ['overview']



@pytest.mark.parametrize('ref', ['../../outside.mp4', '/etc/passwd'])
def test_media_evidence_cannot_escape_result_directory(tmp_path, ref):
    fixture(tmp_path)
    write_json(tmp_path/'one/presentation.json', {'schema_version':'dqs_presentation_v1',
        'channels':[{'video':ref}]})
    catalog=json.loads((tmp_path/'catalog.json').read_text())
    catalog['presentation']='one/presentation.json';write_json(tmp_path/'catalog.json',catalog)
    with pytest.raises(ValueError, match='escapes'):
        media_panel(tmp_path, 'one/presentation.json')


def test_one_and_many_episodes_use_batch_layout_with_inline_details(tmp_path):
    from edc.reporting.batch import page as batch_page

    records = fixture(tmp_path)
    single = report(tmp_path).read_text()
    records += [dict(records[0], object={'type': 'episode', 'id': 'ep2'})]
    (tmp_path/'one/records.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
    multiple = report(tmp_path).read_text()
    batch = batch_page([], dict(robot='r', inputs='', profile='', params='', finished='now', seconds=0, jobs=1))
    for text in (single, multiple, batch):
        assert 'content="edc_batch_report_v1"' in text
        for heading in ('整体结论', '逐条一览', '按算法看问题', '逐条详情'):
            assert heading in text
    assert ' · 1 条 · ' in single and ' · 2 条 · ' in multiple
    assert 'href="#run0-bag0"' in single and 'href="#run0-bag1"' in multiple
    assert 'class="detail"' in single and '算法明细与证据' not in single
    legend = single.split('<b>结论怎么读</b><ul>', 1)[1].split('</ul>', 1)[0]
    assert legend.count('<li>') == 4 and '>参考<' not in legend and '>未完成<' not in legend
    assert 'content="dqs_report_v2"' not in single


def test_snapshot_inventory_not_record_count_defines_episode_rows(tmp_path):
    fixture(tmp_path)
    snapshot = json.loads((tmp_path/'one/run.json').read_text())
    snapshot['dataset']['episodes'] = [
        {'id': 'ep1', 'source': '/data/shared.mcap', 'selector': {'start': 0, 'stop': 10}},
        {'id': 'ep2', 'source': '/data/shared.mcap', 'selector': {'start': 10, 'stop': 20}}]
    snapshot['algorithms'] = [{'id': 'Structure_Check', 'granularity': 'episode'},
                              {'id': 'team__declared_but_missing', 'granularity': 'episode'}]
    write_json(tmp_path/'one/run.json', snapshot)
    page = report(tmp_path).read_text()
    assert ' · 2 条 · ' in page and 'ep2' in page
    assert 'team__declared_but_missing' in page and '没有记录' in page
    assert json.loads((tmp_path/'one/run.json').read_text())['dataset']['episodes'][1]['selector'] == {'start': 10, 'stop': 20}
    assert ' · 0 条 · ' not in page


def test_dataset_plugin_does_not_become_an_episode_or_quality_pass(tmp_path):
    fixture(tmp_path, records=[{'algorithm': {'id': 'team__dataset'},
        'object': {'type': 'dataset', 'id': 'dataset1'}, 'status': 'completed',
        'result': {'reports': [{'verdict': 'ACCEPTABLE', 'score': 99}]}, 'artifacts': []}])
    page = report(tmp_path).read_text()
    assert 'dataset1' not in page
    assert 'dataset1' in (tmp_path/'one/records.jsonl').read_text()
    # The only episode comes from selection.json and has no episode assessment.
    assert ' · 1 条 · ' in page and '不适用' in page
    assert '<strong>1</strong>建议保留' not in page


def test_stale_keep_selection_cannot_hide_partial_scope_or_missing_method(tmp_path):
    fixture(tmp_path, records=[{'algorithm': {'id': 'Action_Smoothness'},
        'object': {'type': 'episode', 'id': 'ep1'}, 'status': 'completed', 'scope_complete': False,
        'result': {'reports': [{'verdict': 'ACCEPTABLE', 'assessment_complete': True}]}, 'artifacts': []}])
    write_json(tmp_path/'one/selection.json', [{'episode_id': 'ep1', 'decision': 'keep', 'assessment_complete': True}])
    snapshot = json.loads((tmp_path/'one/run.json').read_text())
    snapshot['algorithms'] = [{'id': 'Action_Smoothness'}, {'id': 'Sync_Check'}]
    write_json(tmp_path/'one/run.json', snapshot)
    page = report(tmp_path).read_text()
    assert '<strong>1</strong>不适用' in page
    assert '<strong>1</strong>建议保留' not in page
    assert '没有记录' in page


def test_large_raw_results_link_to_unchanged_catalog_records(tmp_path):
    records = [{'algorithm': {'id': 'team__large'}, 'object': {'type': 'episode', 'id': 'ep1'},
        'status': 'completed', 'result': {'reports': [{'samples': list(range(30000))}]},
        'artifacts': ['one/evidence.json']}]
    fixture(tmp_path, records=records)
    write_json(tmp_path/'one/evidence.json', {'source_rows': [1, 2]})
    path = tmp_path/'one/records.jsonl'
    before = path.read_bytes()
    page = report(tmp_path).read_text()
    assert 'href="one/"' in page
    assert 'samples' not in page
    assert len(page) < 50000 and path.read_bytes() == before


def test_history_has_unique_episode_anchors_and_current_run_is_separate(tmp_path):
    fixture(tmp_path, 'first'); fixture(tmp_path, 'second')
    page = report(tmp_path).read_text(); doc = Document(); doc.feed(page)
    ids = [attrs['id'] for _, attrs in doc.tags if 'id' in attrs and 'bag' in attrs['id']]
    assert len(ids) == len(set(ids)) == 1
    assert '运行 second' in page and '历史运行' not in page
    assert 'href="#run1-bag0"' in page
    assert (tmp_path/'first/records.jsonl').exists()


def test_empty_catalog_does_not_invent_passes(tmp_path):
    write_json(tmp_path/'catalog.json', {'schema_version': 'dqs_catalog_v1', 'runs': []})
    page = report(tmp_path).read_text()
    assert '暂无评估记录' in page and ' · 0 条 · ' in page
    assert '<strong>1</strong>建议保留' not in page


def test_report_uses_legacy_labels_without_observation_or_unassessed_chips(tmp_path):
    fixture(tmp_path, records=[{'algorithm': {'id': 'Check_Timestamp'},
        'object': {'type': 'episode', 'id': 'ep1'}, 'status': 'completed',
        'result': {'reports': [{'verdict': 'OBSERVATION', 'assessment_complete': False,
            'reasons': ['缺少设备时钟映射，采集同步未评估']}]}, 'artifacts': []}])
    path = tmp_path/'one/records.jsonl'
    before = path.read_bytes()
    page = report(tmp_path).read_text()
    assert '>参考<' not in page and '未评估完' not in page
    assert '>不适用<' in page and '缺少设备时钟映射' in page
    assert '<strong>1</strong>建议保留' not in page
    assert path.read_bytes() == before
