"""Shared rules turning a built-in algorithm's own report into the common Item (edc.algorithms.base).

Each built-in algorithm folder has a result.py with `explain(report, labels)` (one readable sentence) and
`note(reports, labels)` (short text for the report cell). The rules here are the same for every algorithm:
which recorded verdict means PASS / REVIEW / SALVAGE / EXCLUDE, when an item is not applicable, and how a
salvageable item is repaired. No new scores are computed.
"""
import datetime
import re
from importlib import import_module

from edc.algorithms.base import Item, CHECKED, NOT_APPLICABLE, FAILED, PASS, REVIEW, SALVAGE, EXCLUDE, VERDICTS

TO_VERDICT = {'DO_NOT_USE': EXCLUDE, 'SALVAGE': SALVAGE, 'NEEDS_REVIEW': REVIEW, 'OBSERVATION': REVIEW,
              'ACCEPTABLE': PASS}
REMEDY = {'joint_ik': '整条手臂的关节读数卡住、末端在动：末端位姿是对的，可用 URDF 逆运动学由末端位姿反算这段关节角；'
                      '恢复之前，用关节角训练时这段不能用',
          'fixed_offset': '两组设备的时钟差一个固定值：程序已按这个偏差把时间挪齐，其它检查按挪齐后的时间做；'
                          '请采集方把时钟对准'}
SIDE = {'left': '左臂', 'right': '右臂', 'main': '主臂'}
BOILERPLATE = ('reference topic 为', '共评价', '个非采样', '原始解码画面已检查')


def clock_time(seconds):
    """Absolute data time as Beijing wall-clock time, the way playback tools show it."""
    zone = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.fromtimestamp(seconds, zone).strftime('%H:%M:%S.%f')[:11]


def texts(report):
    return [str(x) for x in (report.get('reasons') or [])] + [str(x) for x in (report.get('warnings') or [])]


def joint_ik_salvage(report):
    """Excluded only because the joint readings stuck while the tip moved: the tip pose is right, so inverse
    kinematics can recover the joint angles."""
    excluded = [c for c in report.get('supplemental_checks') or [] if c.get('verdict') == 'DO_NOT_USE']
    return (report.get('supplemental_verdict') == 'DO_NOT_USE' and report.get('verdict') != 'DO_NOT_USE'
            and bool(excluded) and all(c.get('id') == 'joint_stuck' for c in excluded))


def salvage_of(report):
    if joint_ik_salvage(report):
        return 'joint_ik'
    return ((report.get('clock_diagnosis') or {}).get('salvage') or {}).get('status')


def frames_only(report):
    """Camera stored as separate images: there is no video container to check."""
    return report.get('inspection_object') == 'source_decoded_frames'


def recorded_verdict(report):
    """The algorithm's recorded verdict, with the adapter's own checks and review candidates folded in."""
    from edc.workflows.gates import has_review_candidate
    if report.get('skipped') == 'too_short':
        return 'SKIPPED'
    verdict = report.get('verdict')
    if verdict == 'NOT_APPLICABLE' and report.get('not_applicable'):
        return 'NA'  # The algorithm's own "nothing to check here".
    if report.get('supplemental_verdict') == 'DO_NOT_USE':
        return 'SALVAGE' if joint_ik_salvage(report) else 'DO_NOT_USE'
    if verdict == 'ACCEPTABLE' and has_review_candidate(report):
        return 'NEEDS_REVIEW'
    return 'ACCEPTABLE' if verdict == 'NOT_APPLICABLE' or frames_only(report) and verdict == 'OBSERVATION' else verdict


def clock_sentence(algo, report):
    """The two device groups' clocks disagree: what the clock check found, or None."""
    found = next((t for t in texts(report) if '组间最多相差约' in t), None)
    if not found:
        return None
    gap = re.search(r'组间最多相差约 ([\d.]+) ?(秒|毫秒)', found)
    text = f'相机和机器人本体两组设备的时钟没对准，相差约 {gap.group(1)} {gap.group(2)}' if gap else found
    status = salvage_of(report)
    if status == 'fixed_offset':
        return text + '；但从头到尾一直差这么多（固定偏差），程序已把时间挪齐，其它检查都按挪齐后的时间做了'
    moving = next((t for t in texts(report) if t.startswith('不是固定偏差')), None)
    if algo == 'Check_Timestamp':
        apart = [s for s in report.get('sub_indicators') or []
                 if (m := re.search(r'IoU=([\d.]+)', str(s.get('raw_value', '')))) and float(m.group(1)) == 0]
        return (f'画面和动作配不上：{text}，而且这个差在数据中途还在变，没法挪齐（详见采集同步）'
                + (f'；结果有 {len(apart)} 路和头部相机在时间上完全不重合，记 0 分' if apart else ''))
    return text + (f'；{moving}' if status == 'unstable' and moving else '，跨组时间无法比较')


def target_of(algo, report, labels):
    if algo == 'Video_Integrity':
        name = report.get('input_channel') or ''
    elif algo == 'Numeric_Check' and report.get('input_field'):
        name = f"{report.get('input_channel', '')}.{report['input_field']}"
        channel = report.get('input_channel', '')
        return f"{labels.get(channel, channel)} {report['input_field']}" if channel in labels else name
    else:
        name = SIDE.get(report.get('side')) or report.get('input_channel') or report.get('channel') or ''
    return labels.get(name, name) or '本条'


def dedup_missing_inputs(report):
    """The adapter places whole-scope omissions on its first per-channel report."""
    return (report.get('unavailable_inputs') or []) if any(
        '未完成全通道时间重复检查' in str(t) for t in report.get('reasons') or []) else []


def to_item(algo, report, explain, labels):
    """One built-in report -> Item."""
    from edc.execution.completeness import report_complete
    from edc.reporting.text import clean_reason, findings
    old = recorded_verdict(report)
    target = target_of(algo, report, labels)
    own_dedup_complete = (algo == 'Dedup_Check' and bool(dedup_missing_inputs(report))
                          and bool(report.get('dedup')) and report.get('score') is not None
                          and not (report['dedup'].get('timestamp_dedup') or {}).get('observation_only_extra_count'))
    if own_dedup_complete and old == 'OBSERVATION':
        old = 'ACCEPTABLE'  # Missing sibling channels receive their own unchecked items in aggregation.
    if algo == 'Dedup_Check' and 'At least two recorded samples are required' in texts(report):
        return Item(target, NOT_APPLICABLE, reason='少于2条记录，无法检查时间或序列重复')
    if old == 'SKIPPED':
        short = report.get('duration') or {}
        return Item(target, NOT_APPLICABLE, reason='数据太短，已在结构完整性里排除，这一项不再检查'
                    + (f"（{short['seconds']:.1f} 秒，要求至少 {short['min_duration_s']:g} 秒）" if short else ''))
    if report.get('too_short'):
        short = report['too_short']
        reason = f"数据太短：只有 {short['seconds']:.1f} 秒，本机型要求至少 {short['min_duration_s']:g} 秒；其它 8 项不再检查"
    elif report.get('precondition') == 'clock_fixed_offset':
        reason = '时钟有固定偏差（见采集同步），要先平移时间才能评价对齐；本项暂不判定'
    else:
        reason = (clock_sentence(algo, report) or explain(report, labels)
                  or next((t for t in findings(report, include_time_evidence=False)
                           if not t.startswith(BOILERPLATE)), ''))
    if old == 'NA':
        return Item(target, NOT_APPLICABLE, reason=reason or '从长录制剪出来的切片，两端本来就不在初始位置，不查复位')
    salvage = salvage_of(report)
    if old == 'DO_NOT_USE' and salvage == 'fixed_offset' and not report.get('clock_shift_s'):
        old = 'SALVAGE'  # The fixed clock offset explains the exclusion; time is shifted for the other checks.
    verdict = TO_VERDICT.get(old)
    if verdict is None:
        return Item(target, FAILED, reason=f'算法没有给出结论（{old}）')
    if verdict == PASS and not own_dedup_complete and not report_complete(report, algo):
        verdict, reason = REVIEW, (reason + '；' if reason else '') + '有子项没评估完，需要人看'
    if verdict != PASS and not reason.strip():  # Every verdict but PASS says why; never leave the cell blank.
        score = f"，分数 {report['score']}" if report.get('score') is not None else ''
        reason = f'算法判为{VERDICTS[verdict]}（原始结论 {old}{score}），但没有写出具体原因；请用 --keep 看这条的详细输出'
    remedy = REMEDY['joint_ik' if salvage == 'joint_ik' else 'fixed_offset'] if verdict == SALVAGE else ''
    metrics = {k: report[k] for k in ('score', 'clock_shift_s') if report.get(k) is not None}
    metrics.update(report.get('item_metrics') or {})  # Numbers an algorithm wants shown with its item.
    return Item(target, CHECKED, verdict, clean_reason(reason), remedy, metrics=metrics)


def common_note(algo, reports, items):
    """Cell text every algorithm shares: counts, clock state."""
    if any(r.get('skipped') == 'too_short' for r in reports):
        return ''
    note = ''
    if len(items) > 2:
        from collections import Counter
        from edc.algorithms.base import VERDICTS, STATUSES
        counts = Counter(VERDICTS.get(i.verdict) or STATUSES[i.status] for i in items)
        note = f'{len(items)} 项：' + ' '.join(f'{k}{n}' for k, n in counts.most_common())
    if any(frames_only(r) for r in reports):
        note += ('（' if note else '') + '逐帧图片，无视频容器可查' + ('）' if note else '')
    clock = re.search(r'相差约 ([\d.]+) (秒|毫秒)', ' '.join(i.reason for i in items))
    salvage = next((s for r in reports if (s := salvage_of(r))), None)
    if any(r.get('precondition') == 'clock_fixed_offset' for r in reports):
        note = '等时钟平移后再评'
    elif any(r.get('clock_shift_s') for r in reports):
        note = '已平移时钟后评价'
    elif clock:
        note = f'时钟差约 {clock.group(1)} {clock.group(2)}' + {'fixed_offset': '，可救', 'unstable': '，不能简单平移'}.get(salvage, '')
    return note


def emit(algo, raw, labels):
    """Attach the common Item to every report a built-in algorithm returned (kept as evidence)."""
    module = import_module(f'edc.algorithms.quality.algorithms.{algo}.result')
    reports = raw if isinstance(raw, list) else [raw]
    items = [to_item(algo, r, module.explain, labels) for r in reports]
    note = module.note(reports, items, labels, common_note(algo, reports, items))
    evidence = []
    for r, item in zip(reports, items):
        paired = r.get('record_header_differences')
        if paired is not None:
            # Informational evidence belongs in details, not in the verdict's reason or cell note.
            item.evidence.append({'type': 'record_header_differences', 'data': paired})
        review = r.get('freeze_motion_review')
        if review is not None:
            count = sum(s.get('status') == 'available' for s in review.get('segments', []))
            evidence.append(f"画面冻结联合证据：{count} 个区间可关联运动；{review.get('reason', '')}"
                            '（仅供人工复核，不改变画面判断）')
    if evidence:
        note = '；'.join(filter(None, [note, *evidence]))
    for report, item in zip(reports, items):
        report['edc_item'] = item.__dict__.copy()
    reports[0]['edc_note'] = note
    return raw


def plain_reason(algo, status, reason, notes=None):
    """Why an algorithm did not run, in plain words where the cause is common."""
    reason = str(reason or '')
    notes = notes or {}
    if any(k in reason for k in ('公共行时间', '合成时间', 'Independent acquisition timelines', '原始时间值')):
        return (f"没有各通道各自的原始采集时间：{notes['time']}，这项没有东西可查" if notes.get('time') else
                '没有各通道各自的原始采集时间（所有数据共用一条时间，或时间按帧号生成），这项没有东西可查')
    if algo == 'Reset_Detection' and any(k in reason for k in ('No channels in the declared arm scope', 'Home reference',
                                                                 'calibration')):
        return '机器人 yaml 里没写复位位置（home），不检查复位'
    if algo == 'Action_Smoothness' and 'pose/arm' in reason:
        return '没有末端位置：数据里没录（yaml 没写 eef_position），也没提供 URDF 来算（eef_position: fk）'
    if reason == 'yaml 的 checks.skip 里写了不查这项':
        return reason
    if status == 'missing_input':
        return '机器人 yaml 里没提供这项需要的数据：' + reason
    return reason or status
