"""Present saved value records and their manifest evidence; never rerun a method."""
import json
import os
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from edc.execution.completeness import record_complete
from edc.reporting.text import esc, reports
from edc.schemas import RECORD, require_schema


STYLE = """
.value-summary td{white-space:normal;overflow-wrap:anywhere}.value-summary small{display:block;color:#586c7c}
.value-report details{background:white;border-radius:10px;padding:10px 16px;margin:8px 0}
.value-report summary{cursor:pointer;font-weight:600}.value-report .chip{display:inline-block;padding:1px 8px;border-radius:5px;font-size:12px;font-weight:600}
.value-report .ok{background:#e3f4ea;color:#1d6b3a}.value-report .warn{background:#fff2d6;color:#7a5200}
.value-report .bad{background:#fde4e2;color:#9a2f2a}.value-report .na{background:#eef1f3;color:#66737d}
"""
STATUS = {'completed': '处理完成', 'processing_error': '处理失败',
          'missing_input': '缺少输入', 'missing_dependency': '缺少依赖',
          'not_applicable': '不适用', 'incomplete': '未完成',
          'not_requested': '未启用', 'provisional': '条件结论（待确认）'}
VERDICT = {'PASS': '通过', 'FAIL': '未通过', 'NEEDS_REVIEW': '需复核',
           'DO_NOT_USE': '未通过', 'REVIEW': '需复核', 'EXCLUDE': '未通过', 'SALVAGE': '可救'}


def _file(output, name, boundary):
    """Accept only manifest-relative, existing files within this algorithm run."""
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        return None
    try:
        (output / name).relative_to(boundary)
        resolved = (output / name).resolve()
        resolved.relative_to(boundary.resolve())
        resolved.relative_to(output.resolve())
        return resolved if resolved.is_file() else None
    except (ValueError, OSError, RuntimeError):
        return None


def _url(path, report_dir):
    return quote(os.path.relpath(path, report_dir).replace(os.sep, '/'), safe='/')


def _link(path, report_dir, label):
    return f'<a href="{esc(_url(path, report_dir))}">{esc(label)}</a>'


def _short(value, limit=180):
    text = str(value).replace('\n', ' ')
    return text if len(text) <= limit else text[:limit] + '…（详见结果）'


def _number(value):
    return f'{value:.6g}' if isinstance(value, (int, float)) else '—'


def _reason(record, native):
    reasons = [record['reason']] if record.get('reason') else []
    for report in native:
        distribution = report.get('distribution')
        if isinstance(distribution, dict):
            reasons.append(f"评估 {distribution.get('total_episodes', '?')} 条轨迹，"
                           f"{len(distribution.get('categories', {}))} 个类别；分数为原算法类别平均")
            reasons.extend(distribution.get('warnings') or [])
        evaluation = report.get('evaluation') or {}
        if evaluation:
            tasks = evaluation.get('subtask_results') or []
            reasons.append(f"子任务 {evaluation.get('evaluated_subtasks', len(tasks))} / "
                           f"{evaluation.get('total_subtasks', len(tasks))}；"
                           f"回退候选 {sum(t.get('regression_count', 0) for t in tasks)}，"
                           f"停滞候选 {sum(t.get('stagnation_count', 0) for t in tasks)}")
        for key, component in (report.get('components') or {}).items():
            if component.get('status') not in {'completed', 'not_requested'}:
                label = {'physical': '物理归因', 'visual': '视觉评估', 'segmentation': '分段', 'filter': '筛选'}.get(key, key)
                limitations = component.get('limitations') or {}
                explanation = component.get('reason')
                if not explanation and isinstance(limitations, dict):
                    explanation = next((str(item) for rows in limitations.values() for item in rows), None)
                reasons.append(label + '：' + str(explanation or STATUS.get(component.get('status'), '未完成')))
        if report.get('reason'):
            reasons.append(str(report['reason']))
    if record.get('scope_complete') is False or record.get('withheld_episodes'):
        reasons.insert(0, '所选输入未全部完成评估')
    return _short('；'.join(reasons) or '见原始结果中的指标、依据和限制')


def load_records(output, run_dir, specs):
    """Read once for both report locations, including failure records."""
    loaded = []
    for spec in specs:
        ident = spec['id']
        folder = run_dir / ident
        filename = folder / 'records.jsonl'
        path = _file(output, filename.relative_to(output).as_posix(), run_dir)
        if path is None:
            loaded.append((spec, None, None))
            continue
        with path.open(encoding='utf-8') as stream:
            count = 0
            for line in stream:
                if line.strip():
                    record = require_schema(json.loads(line), RECORD)
                    if record['algorithm']['id'] != ident:
                        raise ValueError('Report record algorithm differs from run metadata')
                    loaded.append((spec, record, path))
                    count += 1
            if not count:
                loaded.append((spec, None, None))
    return loaded


def sections(output, run_dir, report_dir, loaded):
    objects, details, columns = {}, [], {}
    for index, (spec, record, record_path) in enumerate(loaded):
        columns[spec['id']] = spec.get('name') or spec['id']
        anchor = f'value-result-{index}'
        if record is None:
            objects.setdefault('结果记录不可用', {}).setdefault(spec['id'], []).append(
                '<span class="chip warn">未完成</span><small>缺少结果记录</small>')
            continue
        obj = record.get('object') or {}
        native = reports(record)
        complete = record_complete(record)
        explicit = bool(native) and all(r.get('assessment_complete') is True for r in native)
        state = STATUS.get(record.get('status'), record.get('status', '未知'))
        state += '；评估' + ('完成' if complete and explicit else '未完成' if not complete else '未声明')
        scores = [_number((r.get('distribution') or {}).get('score', r.get('score'))) for r in native]
        scores = [s for s in scores if s != '—']
        # Display only method-authored judgments; completion is not a quality verdict.
        verdicts = [VERDICT.get(r.get('verdict'), r.get('verdict')) for r in native if r.get('verdict')]
        verdict = '未完成' if not complete else ' / '.join(dict.fromkeys(verdicts)) or '未设判定'
        links = [_link(record_path, report_dir, '完整结果')]
        unavailable = 0
        linked = set()
        for name in record.get('artifacts', []):
            path = _file(output, name, run_dir / spec['id'] / 'artifacts')
            if path is None:
                unavailable += 1
                continue
            if path.name == 'attribution_report.txt' and path.name not in linked:
                links.append(_link(path, report_dir, '归因说明'))
                linked.add(path.name)
        reason = _reason(record, native)
        if unavailable:
            reason += f'；{unavailable} 个证据文件不可用，见完整结果清单'
        label = '数据集' if obj.get('type') == 'dataset' else '轨迹'
        score = ' / '.join(scores) or '—'
        tone = 'ok' if verdict == '通过' else 'bad' if verdict == '未通过' else 'warn' if verdict in {'未完成', '需复核', '可救'} else 'na'
        if record.get('status') == 'processing_error':
            tone = 'bad'
        chip = f'<span class="chip {tone}">{esc(verdict)}</span>'
        cell = (f'<a href="#{anchor}">{chip}</a><small>原生分数：{esc(score)}</small>'
                f'<small>{esc(_short(reason, 70))}</small>')
        object_label = f"{label}：{obj.get('id', '未知')}"
        objects.setdefault(object_label, {}).setdefault(spec['id'], []).append(cell)
        details.append(f'<details id="{anchor}"><summary>{esc(object_label)} · {esc(columns[spec["id"]])} {chip}</summary>'
                       f'<p>执行状态：{esc(state)}；原生分数：{esc(score)}</p><p>{esc(reason)}</p>'
                       f'<p>{" · ".join(links)}</p></details>')
    rows = ['<tr><th>' + esc(obj) + '</th>' + ''.join('<td>' + '<hr>'.join(cells.get(ident, [])) + '</td>'
            for ident in columns) + '</tr>' for obj, cells in objects.items()]
    return ('<section class="value-report"><h2>价值算法结果</h2><p>分数与判定沿用算法原始输出；未设通过阈值时不补造判定，'
            '评估未完成不等于数据不合格。详细指标、图表和证据见完整结果。</p>'
            '<h3>逐条一览</h3><div class="scroll"><table class="value-summary"><thead><tr><th>数据</th>' +
            ''.join(f'<th>{esc(name)}</th>' for name in columns.values()) +
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
            '<h3>逐条详情</h3>' + ''.join(details) + '</section>')


def page(content, metadata):
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>价值评估报告</title>
<style>body{{background:#f3f6f8;color:#192d3d;font:15px/1.6 system-ui,sans-serif}}
main{{max-width:1300px;margin:24px auto;padding:0 16px}}a{{color:#155f83}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #dce5eb}}
{STYLE}</style></head><body><main><h1>价值评估报告</h1><p>运行：{esc(metadata['run_id'])}</p>{content}</main></body></html>'''
