import json
from pathlib import Path
from html.parser import HTMLParser
import pytest
from edc.reporting.results import report, write_json


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
    fixture(tmp_path)
    page=report(tmp_path).read_text();doc=Document();doc.feed(page)
    visible=''.join(doc.text)
    assert '95.5' in visible and '相机未找到配对' in visible
    assert '不能只看评分' in visible
    assert '整体尚未评估完成' in visible and '同一时钟下的独立采集时间轴' in visible
    assert '拆分视图，非新增样本' in visible
    assert 'schema_version' not in visible and 'schema_version' in ''.join(doc.raw)
    assert all('open' not in attrs for tag,attrs in doc.tags if tag=='details' and attrs.get('class')=='raw')


def test_report_keeps_runs_separate_latest_first(tmp_path):
    fixture(tmp_path,'first');fixture(tmp_path,'second')
    page=report(tmp_path).read_text()
    assert page.index('运行 second')<page.index('历史运行 · first')
    assert page.count('class="history"')==1
    assert page.count('整体尚未评估完成')==2


def test_report_does_not_turn_missing_or_error_into_pass(tmp_path):
    records=[{'algorithm':{'id':'team__unknown'},'object':{'type':'episode','id':'ep1'},
              'status':'processing_error','reason':'<script>unsafe</script>','result':None,'artifacts':[]}]
    fixture(tmp_path,records=records)
    page=report(tmp_path).read_text()
    assert '执行失败' in page and '整体尚未评估完成' in page
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
    assert 'keep evidence' in report(tmp_path).read_text()


def test_video_subresult_warning_not_hidden_by_top_level_score(tmp_path):
    records=[{'algorithm':{'id':'Video_Integrity'},'object':{'type':'episode','id':'ep1'},'status':'completed',
              'result':{'reports':[{'score':100,'verdict':'ACCEPTABLE','frame_quality':{
                  'verdict':'NEEDS_REVIEW','reasons':['模糊帧占比 3%']}}]},'artifacts':[]}]
    fixture(tmp_path,records=records)
    page=report(tmp_path).read_text();doc=Document();doc.feed(page)
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
        assert 'content="dqs_report_v2"' in page
        assert '尚未进行参数确认：请作者完成这一步' in page
        assert '<script>unsafe' not in page
        # Historical records do not acquire invented zero counts.
        assert '未记录' in ''.join(doc.text)
        if index:
            assert 'data-seconds="4.5"' in page and 'one/image.jpg' in page
        else:
            assert '没有附加可播放的视频证据' in page
    assert layouts[0] == layouts[1] == ['overview','configuration','numeric-fields','algorithms','media','architecture']


@pytest.mark.parametrize('ref', ['../../outside.mp4', '/etc/passwd'])
def test_media_evidence_cannot_escape_result_directory(tmp_path, ref):
    fixture(tmp_path)
    write_json(tmp_path/'one/presentation.json', {'schema_version':'dqs_presentation_v1',
        'channels':[{'video':ref}]})
    catalog=json.loads((tmp_path/'catalog.json').read_text())
    catalog['presentation']='one/presentation.json';write_json(tmp_path/'catalog.json',catalog)
    with pytest.raises(ValueError, match='escapes'):
        report(tmp_path)
