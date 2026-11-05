"""One HTML page across many episodes. Shows the common results (edc.algorithms.base); judges nothing itself."""
import json
from collections import Counter
from pathlib import Path

from edc.algorithms.base import AlgorithmResult, decide
from edc.algorithms.quality.adapters.cpu import ORDER
from edc.algorithms.quality.adapters.results import guide
from edc.reporting.text import algorithm_names, clean_reason, esc

# Worst first: (word, colour, meaning).
LEVEL = {'EXCLUDE': ('排除', 'bad', '算法认为这一项的数据不能用'),
         'SALVAGE': ('可救', 'fix', '有问题，但原因清楚、能修好：格子里写了怎么修'),
         'failed': ('没跑完', 'bad', '算法出错或没有结论，见原因'),
         'REVIEW': ('复核', 'warn', '发现可疑之处，也可能是正常情况，需要人看'),
         'PASS': ('通过', 'ok', '这一项没有问题'),
         'not_applicable': ('不适用', 'na', '这条数据没有这项可查（例如没有原始采集时间、yaml 没写复位位置、数据太短），见原因')}
DECISION = {'exclude': ('含建议排除项', 'bad', '至少一项算法判为“排除”'),
            'salvage': ('可救', 'fix', '没有排除项，但有“可救”的问题：修好后可用'),
            'unassessed': ('未评估完', 'info', '没有排除和可救项，但有算法没跑完，框架不下结论'),
            'review': ('建议复核', 'warn', '没有排除项，但有需要人看的项'),
            'keep': ('建议保留', 'ok', '全部通过（“不适用”的项不影响）'),
            'failed': ('运行失败', 'bad', 'edc run 没有正常结束，见该条详情里的日志')}
PROBLEM = ('EXCLUDE', 'SALVAGE', 'failed', 'REVIEW')


def summarize(output):
    """{decision, algos: {algorithm: result dict}} from the quality.json of a single-episode `edc run`, or None."""
    runs = sorted(Path(output).glob('_runs/*/'))
    if not runs or not (runs[-1] / 'quality.json').exists():
        return None
    data = json.loads((runs[-1] / 'quality.json').read_text(encoding='utf-8'))
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError('quality.json 应包含一条轨迹的结果')
    episode = data[0]
    results = episode.get('results')
    if episode.get('decision') not in DECISION or not isinstance(results, list) or not results:
        raise ValueError('quality.json 缺少有效结论或算法结果')
    algos = {}
    for raw in results:
        result = AlgorithmResult.from_dict(raw)
        if (not isinstance(result.algorithm, str) or not result.algorithm or not result.items
                or result.algorithm in algos or raw.get('level') != result.level
                or any(not isinstance(i.target, str) or not isinstance(i.reason, str) for i in result.items)):
            raise ValueError('quality.json 的算法结果不完整或不一致')
        algos[result.algorithm] = raw
    if episode['decision'] != 'failed' and episode['decision'] != decide(
            [AlgorithmResult.from_dict(r) for r in algos.values()]):
        raise ValueError('quality.json 的轨迹结论与算法结果不一致')
    return {'decision': episode['decision'], 'algos': algos}


def problems(result):
    """[(target, level, text)] worth showing: everything but plain passes."""
    out = []
    for item in result['items']:
        level = item['verdict'] if item['status'] == 'checked' else item['status']
        if level != 'PASS':
            text = clean_reason(item['reason'])
            if item.get('remedy'):
                text = text.rstrip('。；;') + f'。怎么修：{item["remedy"]}'
            out.append((item['target'], level, text))
    return out


def chip(verdict):
    word, tone, meaning = LEVEL.get(verdict, (str(verdict), 'na', ''))
    return f'<span class="chip {tone}" title="{esc(meaning)}">{esc(word)}</span>'


def short_note(result):
    """Keep source-selection bookkeeping in JSON, not the quality summary."""
    note = result.get('note', '').partition('本次未检查：')[0].rstrip('；')
    if any(i['status'] == 'failed' for i in result['items']) and ('没跑完' in note or not note):
        counts = Counter(i['verdict'] if i['status'] == 'checked' else i['status'] for i in result['items'])
        parts = [f'{LEVEL[v][0]}{n}项' for v, n in counts.items() if v != 'failed']
        parts.append(f'{counts["failed"]}项原因见详情')
        return '，'.join(parts)
    return note


def readable_log(bag):
    """Keep machine summaries in JSON; preserve actual error/traceback text in HTML."""
    log = bag.get('log', '')
    try:
        summary = json.loads(log)
    except (ValueError, TypeError):
        return log
    if (isinstance(summary, dict) and isinstance(summary.get('run_id'), str)
            and isinstance(summary.get('status_counts'), dict)
            and set(summary) <= {'run_id', 'episodes', 'status_counts', 'status'}
            and summary.get('status') in ('success', 'partial_success', 'failed')):
        code = bag.get('returncode')
        return ('检测进程退出码：' + str(code) + '。' if code is not None else '') + '具体原因见各算法详情，完整日志保存在运行记录中。'
    return log


def page(bags, info, *, algorithm_ids=None, title='批量质检报告'):
    rank = list(DECISION)
    bags = sorted(bags, key=lambda b: (rank.index(b['decision']) if b['decision'] in rank else 9, b['name']))
    decisions = Counter(b['decision'] for b in bags)
    cards = ''.join(f'<div class="card {DECISION[d][1]}"><strong>{decisions[d]}</strong>{DECISION[d][0]}'
                    f'<small>{DECISION[d][2]}</small></div>' for d in DECISION if decisions[d])
    levels = {c['level'] for b in bags for c in b['algos'].values()}
    legend = ''.join(f'<li>{chip(v)} {esc(LEVEL[v][2])}</li>'
                     for v in LEVEL if (v in ('PASS', 'REVIEW', 'SALVAGE', 'EXCLUDE') or v in levels))
    algos = (list(algorithm_ids) if algorithm_ids is not None else
             list(ORDER) + sorted({a for b in bags for a in b['algos']} - set(ORDER)))
    names = algorithm_names()
    head = ''.join(f'<th>{esc(names.get(a, a))}</th>' for a in algos)
    rows, details, issues = [], [], {a: [] for a in algos}
    for i, b in enumerate(bags):
        word, tone, _ = DECISION.get(b['decision'], (b['decision'], 'na', ''))
        name = f'<a href="#bag{i}">{esc(b["name"])}</a> <span class="chip {tone}">{esc(word)}</span>'
        tds, lines = [], []
        for algo in algos:
            c = b['algos'].get(algo)
            if c is None:
                tds.append('<td></td>')
                continue
            found = problems(c)
            tip = '\n'.join(f'{w}：{r}' for w, v, r in found)
            tds.append(f'<td title="{esc(tip)}">{chip(c["level"])}<small>{esc(short_note(c))}</small></td>')
            if c['level'] in PROBLEM:
                issues[algo].append((i, b, c))
            reasons = ''.join(f'<li>{chip(v)} <b>{esc(w)}</b> {esc(r)}</li>' for w, v, r in found)
            lines.append(f'<tr><td>{esc(names.get(algo, algo))}</td><td>{chip(c["level"])} '
                         f'<span class="muted">{esc(short_note(c))}</span>' + (f'<ul>{reasons}</ul>' if reasons else '')
                         + '</td></tr>')
        rows.append(f'<tr><th>{name}</th>' + ''.join(tds) + '</tr>')
        extra = f'<table class="detail">{"".join(lines)}</table>' if b['algos'] else ''
        if b.get('log') or b['decision'] == 'failed':
            extra += f'<pre>{esc(readable_log(b))}</pre>'
        kept = f'<p class="muted">完整框架输出（含单条报告和视频证据）：{esc(b["kept"])}</p>' if b.get('kept') else ''
        details.append(f'<details id="bag{i}"><summary>{esc(b["name"])} <span class="chip {tone}">{esc(word)}</span> '
                       f'<span class="muted">{str(b["size_mb"]) + " MB · " if b.get("size_mb") is not None else ""}用时 {b["seconds"]:.0f} 秒</span></summary>'
                       f'<p class="muted">{esc(b["source"])}</p>{kept}{extra}</details>')
    sections = []
    for algo in algos:
        found = issues[algo]
        tally = Counter(LEVEL[c['level']][0] for _, _, c in found if c['level'] != 'failed')
        summary_parts = [f'{k} {n} 条' for k, n in tally.items()]
        without_verdict = sum(c['level'] == 'failed' for _, _, c in found)
        if without_verdict:
            summary_parts.append(f'{without_verdict} 条原因见详情')
        summary = '，'.join(summary_parts) or '没有排除、可救或复核项'
        body = ''.join(f'<li><a href="#bag{i}">{esc(b["name"])}</a> {chip(c["level"])} <span class="muted">{esc(short_note(c))}</span><ul>'
                       + ''.join(f'<li><b>{esc(w)}</b> {esc(r)}</li>' for w, v, r in problems(c) if v in PROBLEM) + '</ul></li>'
                       for i, b, c in found)
        text = guide(algo) if any(item['verdict'] in ('REVIEW', 'SALVAGE', 'EXCLUDE')
                                  for _, _, result in found for item in result['items']) else ''
        how = f'<p class="guide">怎么复核：{esc(text)}</p>' if text else ''
        sections.append(f'<details><summary>{esc(names.get(algo, algo))} <span class="muted">{summary}</span></summary>'
                        f'{how}<ul>{body}</ul></details>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)} · {esc(info['robot'])}</title><style>
body{{margin:0;background:#f3f6f8;color:#192d3d;font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:1400px;margin:24px auto;padding:0 16px}}h1{{margin:0 0 4px}}h2{{margin:28px 0 10px;font-size:18px}}
.muted{{color:#586c7c;font-size:13px}}a{{color:#155f83}}.box{{background:#fff;border-radius:10px;padding:10px 18px}}
.box ul{{margin:4px 0;padding-left:18px}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}
.card{{background:#fff;border-radius:10px;padding:12px 16px;border-left:5px solid;min-width:200px;flex:1}}
.card strong{{font-size:28px;margin-right:8px}}.card small{{display:block;color:#586c7c}}
.scroll{{overflow-x:auto;background:#fff;border-radius:10px}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:6px 8px;border-bottom:1px solid #e3eaef;text-align:left;vertical-align:top}}
.scroll td,.scroll th{{white-space:nowrap}}thead th{{background:#eef3f6;position:sticky;top:0}}tbody th{{font-weight:500}}
td small{{display:block;color:#586c7c;font-size:12px}}tr:hover td,tr:hover th{{background:#f7fafc}}
.chip{{display:inline-block;padding:1px 8px;border-radius:5px;font-size:12px;font-weight:600;cursor:help}}
.ok{{background:#e3f4ea;color:#1d6b3a;border-color:#3b9b5e}}.warn{{background:#fff2d6;color:#7a5200;border-color:#d89b1a}}
.bad{{background:#fde4e2;color:#9a2f2a;border-color:#d0524a}}.info{{background:#e6eef8;color:#2d527f;border-color:#5b86bd}}
.na{{background:#eef1f3;color:#66737d;border-color:#aab4bb}}.fix{{background:#f1e8fb;color:#5b2d8a;border-color:#8b5cc4}}
.guide{{background:#f7f9fb;border-left:3px solid #5b86bd;padding:6px 10px;margin:8px 0;font-size:13px}}
details{{background:#fff;border-radius:10px;margin:8px 0;padding:10px 16px}}summary{{cursor:pointer;font-weight:600}}
details ul{{margin:6px 0;padding-left:18px}}details li{{margin:3px 0}}.detail td:first-child{{width:150px;white-space:nowrap}}
pre{{white-space:pre-wrap;background:#f4f6f8;padding:10px;font-size:12px}}
</style></head><body><main>
<h1>{esc(title)} · {esc(info['robot'])}</h1>
<p class="muted">{esc(info['inputs'])} · {len(bags)} 条 · {esc(info['finished'])} 完成，用时 {info['seconds']:.0f} 秒（同时 {info['jobs']} 条）<br>
机器人配置 {esc(info['profile'])}<br>
每条由框架 <code>edc run</code> 运行所选算法；本页只汇总算法写下的结论和原因，不计算新分数、不做总分。</p>
<div class="box"><b>结论怎么读</b><ul>{legend}</ul>
<span class="muted">每个算法对每一项（一条手臂、一路相机、一个字段）给出 通过／复核／可救／排除 之一，没法检查的写明原因。
表格格子显示该算法最差的一项。整条的结论：有“排除”就是“含建议排除项”；否则有“可救”就是“可救”；
否则有“复核”就是“建议复核”；其余是“建议保留”。“不适用”不影响结论。</span></div>
<h2>整体结论</h2><div class="cards">{cards}</div>
<h2>逐条一览 <span class="muted">点名字看该条详情，鼠标停在格子上看原因</span></h2>
<div class="scroll"><table><thead><tr><th>数据</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>按算法看问题 <span class="muted">只列排除和复核</span></h2>{''.join(sections)}
<h2>逐条详情</h2>{''.join(details)}
</main></body></html>'''
