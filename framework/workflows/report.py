"""Offline stage overview; original method reports remain independently available."""
import html
import json
from pathlib import Path


def render_workflow(root, manifest, output):
    esc = lambda value: html.escape(str(value), quote=True)
    labels = {'quality': '质量评估', 'editing': '数据编辑', 'value': '价值评估', 'augmentation': '数据增强'}
    cards = []
    for stage in manifest['stages']:
        gates = stage.get('gate', {})
        body = '<br>'.join(esc(k) + '：' + esc(v['decision']) + ' ' + esc('；'.join(v['reasons'])) for k, v in gates.items())
        cards.append('<article><h2>' + esc(labels[stage['kind']]) + ' · ' + esc(stage['id']) + '</h2><p>' +
            esc(stage['status']) + (' · 复用已有执行' if stage.get('reused') else '') + '</p><p>输入：' +
            esc(stage['input']) + ' · 选中轨迹：' + esc(len(stage.get('selected_episodes', []))) + '</p><p>' +
            esc(stage.get('reason', '')) + '</p><p>' + body + '</p><pre>' +
            esc(json.dumps(stage.get('transform_errors', []), ensure_ascii=False, indent=2)) + '</pre></article>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>具身数据整理工作流</title><style>body{margin:0;background:#f2f6f8;color:#183341;font:16px/1.65 system-ui}main{max-width:1100px;margin:40px auto;padding:0 20px}h1{margin-bottom:5px}.flow{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}.flow span{background:#166c70;color:white;border-radius:8px;padding:12px 18px}article{background:white;border:1px solid #d7e3e7;border-radius:12px;padding:22px;margin:16px 0;overflow-wrap:anywhere}h2{font-size:20px}pre{white-space:pre-wrap;font-size:13px}a{color:#12696e}</style><main>
<h1>具身数据整理工作流</h1><p>Embodied Data Curation Framework</p><div class="flow"><span>统一接入</span>→<span>质量评估</span>→<span>价值评估</span>→<span>可选增强与复检</span></div>
<p>编辑副本需重新质量评估。处理状态、数据达标与人工复核分别记录，各方法保留原始输出。</p>'''
    page += '<p>工作流：' + esc(manifest['workflow_id']) + ' · ' + esc(manifest['status']) + '</p>'
    page += '<p><a href="../../report.html">查看固定版式算法报告</a> · <a href="workflow.json">完整运行记录</a></p>'
    page += ''.join(cards) + '</main></html>'
    (Path(root) / 'report.html').write_text(page)
