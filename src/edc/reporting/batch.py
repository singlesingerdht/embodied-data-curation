"""One HTML page across many `edc run` outputs. Collects recorded verdicts; computes no new score."""
import json
import math
import re
from collections import Counter
from pathlib import Path

from edc.reporting.html import NAMES, esc, findings, reports
from edc.execution.completeness import report_complete
from edc.workflows.gates import has_review_candidate

ALGOS = ['Structure_Check', 'Numeric_Check', 'Action_Smoothness', 'Check_Timestamp', 'Sync_Check',
         'Root_Cause', 'Dedup_Check', 'Reset_Detection', 'Video_Integrity']
# Worst first: (short word, colour, meaning). NOT_APPLICABLE is shown as a pass: the item does not
# need this check, and the framework's own gate already counts it as covered.
LEVEL = {'missing': ('未完成', 'bad', '算法没跑完，没有结论'),
         'DO_NOT_USE': ('排除', 'bad', '该项建议排除：算法认为这一项的数据不能用'),
         'SALVAGE': ('可救', 'fix', '问题原因清楚、能修：设备时钟差了一个固定值（程序已把时间挪齐，其它检查都按挪齐后的时间做了，要请采集方把时钟对准）；'
                                    '或关节读数卡住（末端位姿是对的，可用逆运动学反算出关节角，恢复前这段关节角不能用）'),
         'NEEDS_REVIEW': ('复核', 'warn', '建议复核：发现可疑之处，但也可能是正常情况，需要人看'),
         'OBSERVATION': ('参考', 'info', '仅供参考：条件不够，算法只报告观察到的现象，不判好坏'),
         'ACCEPTABLE': ('通过', 'ok', '该项可接受（不需要做这项检查的字段也算通过；相机存成逐帧图片时没有视频容器可查，画面正常即算通过）'),
         'NA': ('不适用', 'na', '这一项对这条数据没有东西可查：所有通道和画面共用一条时间（导出时已逐帧对齐），没有各自的原始时间可比；'
                              '或这条是从长录制剪出来的切片（数据集自带剪辑记录），开头和结尾本来就不在初始位置，不查复位'),
         'SKIPPED': ('不检查', 'na', '数据太短，已在结构完整性里排除，这一项不再检查'),
         None: ('无结论', 'na', '没有结论')}
ORDER = list(LEVEL)
DECISION = {'exclude': ('含建议排除项', 'bad', '至少一项算法判为“排除”'),
            'salvage': ('可救', 'fix', '只因能修的问题被排除：时钟固定偏差（按偏差平移时间后可重新检查），'
                                       '或关节读数卡住（用末端位姿逆运动学恢复关节角）'),
            'review': ('建议复核', 'warn', '没有排除项，但有需要人看的项'),
            'unassessed': ('未评估完', 'info', '没有排除项，但有检查没评估完整（给了“参考”或只做了部分子项），框架不下“保留”结论'),
            'keep': ('建议保留', 'ok', '所选算法全部通过'),
            'failed': ('运行失败', 'bad', 'edc run 没有正常结束，见该条详情里的日志')}
SIDE = {'left': '左臂', 'right': '右臂'}
QUALITY = {'black_ratio': '黑屏', 'white_ratio': '白屏', 'garbled_ratio': '花屏', 'freeze_ratio': '画面冻结',
           'occlusion_ratio': '遮挡', 'blur_ratio': '模糊', 'overexpo_ratio': '过曝', 'underexpo_ratio': '欠曝',
           'color_anomaly_ratio': '偏色'}
BOILERPLATE = ('reference topic 为', '共评价', '个非采样', '原始解码画面已检查')
CLOCK_ALGOS = {'Sync_Check', 'Check_Timestamp'}
# What a person should look at for each algorithm's 复核, shown above its list of problems.
GUIDE = {
    'Numeric_Check': '在回放工具里跳到所列时刻（北京时间）看手臂。速度超限：确实在快速运动就放过，只有一两帧突然跳变则是数据毛刺。'
                     '数值连续不变：持续几毫秒到几十毫秒一般是正常停住或重复发送，持续很久且画面里手臂在动才是冻结。'
                     '关节读数卡住（X1 Pro）判“可救”：整条手臂 6 个关节读数不变、但末端位置和画面在动，是关节数据没更新。'
                     '末端位置和姿态是对的（不卡住时，用厂家 URDF 从关节角算出的末端和记录值相差 1–2 mm、1–2°），'
                     '所以可以用逆运动学从末端位姿反算出这段关节角；恢复之前，用关节角训练时这段不能用，只用末端位姿训练时不受影响。',
    'Check_Timestamp': '每一路单独打分，整条结论看所有路的平均分：高于 90 通过，80–90 复核，80 及以下排除。'
                       '对每帧画面，找时间上最近的那条动作消息，两者的时间差叫拼接误差；P99 是最差 1% 的拼接误差。'
                       '每条原因里写了本机型的扣分起点。P99 小于相机半帧（30 Hz 时约 17 毫秒）时，配对影响很小；'
                       '和头部相机录制时间段重合不到 95% 的一路直接记 0 分，常见于很短的数据。'
                       '复核时看“有明显空档”的时刻是否正好落在关键动作上。'
                       '所有数据和画面共用一条时间的格式（如 X1 Pro 轨迹 JSON），对齐在导出时已经做完，这里只查有没有跳帧：'
                       '相邻两行相隔超过本机型的上限算空档，进复核；跳到所列时刻看画面是否突然跳一下、是否正好落在关键动作上。',
    'Sync_Check': '“可救”：两组设备的时钟差了一个固定值，程序已自动把时间挪齐，其它检查按挪齐后的时间做，不需要人做什么；'
                  '只要把“哪些天、哪台机器时钟没对准”反馈给采集方。“排除”：时钟差在数据中途还在变，没法挪齐，这条数据的画面和动作对不上。',
    'Action_Smoothness': '算法只看手“停住”的时段：手停住时位置本该不动，还在细微抖动就扣分。抖动指标是位置的三阶导数（jerk，米/秒³），'
                         '按 30 Hz 算；满分线 5 和零分线 15 是算法作者的设定。复核时回放所列手臂停下来的时候，看夹爪相机画面是否在抖：'
                         '画面稳就放过，明显抖动说明遥操作或控制有抖动。',
    'Root_Cause': '看所列时刻附近各路数据是否同时缺失：同时缺失说明整机卡顿，只缺一路说明是那一路设备的问题。',
    'Reset_Detection': '开头或结尾的手臂姿态和初始位置差得多（超过本机型容差：乐聚按关节角 0.6 rad，约 34°；X1 Pro 按手末端位置 5 cm）。跳到所列时刻，和一条复位检测“通过”的数据的开头画面对比，看所列关节（或末端方向）：'
                       '① 手臂明显不在初始姿态，而这批任务要求从初始姿态开始和结束：录早了或录晚了，开头或结尾不完整，排除或裁掉；'
                       '② 手臂其实已在初始姿态，只是这个关节差一点：初始姿态或 0.6 rad 容差要改，告诉配置人；'
                       '③ 任务本来就不要求复位：放过。',
    'Video_Integrity': '每 5 帧抽 1 帧检查。某一类问题（如模糊、遮挡）占抽检帧的 30% 及以上判排除；'
                       '不到 30% 判通过，格子里备注问题帧数。排除的可以看一眼所列相机，确认是不是真的成片模糊或被挡住。',
}


def clock_time(seconds):
    """Absolute data time as Beijing wall-clock time, the way playback tools show it."""
    import datetime
    zone = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.fromtimestamp(seconds, zone).strftime('%H:%M:%S.%f')[:11]


def salvage_of(item):
    if joint_ik_salvage(item):
        return 'joint_ik'
    return ((item.get('clock_diagnosis') or {}).get('salvage') or {}).get('status')


def joint_ik_salvage(item):
    """Excluded only because the joint readings stuck while the tip moved: the tip pose is right, so inverse
    kinematics can recover the joint angles."""
    excluded = [c for c in item.get('supplemental_checks') or [] if c.get('verdict') == 'DO_NOT_USE']
    return (item.get('supplemental_verdict') == 'DO_NOT_USE' and item.get('verdict') != 'DO_NOT_USE'
            and bool(excluded) and all(c.get('id') == 'joint_stuck' for c in excluded))


def frames_only(item):
    """Camera stored as separate images: there is no video container to check."""
    return item.get('inspection_object') == 'source_decoded_frames'


def verdict_of(item):
    if item.get('skipped') == 'too_short':
        return 'SKIPPED'
    verdict = item.get('verdict')
    if verdict == 'NOT_APPLICABLE' and item.get('split_segment'):  # Cut-out segment: no real recording end to check.
        return 'NA'
    if item.get('supplemental_verdict') == 'DO_NOT_USE':  # The gate excludes on an adapter check's exclusion.
        return 'SALVAGE' if joint_ik_salvage(item) else 'DO_NOT_USE'
    # The framework's gate also counts review candidates (e.g. freeze candidates) on a passing item.
    if verdict == 'ACCEPTABLE' and has_review_candidate(item):
        return 'NEEDS_REVIEW'
    # Older outputs gave image-only cameras with clean frames OBSERVATION; adapters/video.py now says ACCEPTABLE.
    return 'ACCEPTABLE' if verdict == 'NOT_APPLICABLE' or frames_only(item) and verdict == 'OBSERVATION' else verdict


def explain(algo, item, names=None):
    """One readable sentence from what the algorithm itself recorded; no new judgement."""
    return _explain(algo, item, names or {}) or next(iter(findings(item)), '')


def _explain(algo, item, names):
    texts = [str(x) for x in (item.get('reasons') or [])] + [str(x) for x in (item.get('warnings') or [])]
    if item.get('too_short'):
        short = item['too_short']
        return f"数据太短：只有 {short['seconds']:.1f} 秒，本机型要求至少 {short['min_duration_s']:g} 秒；其它 8 项不再检查"
    if item.get('precondition') == 'clock_fixed_offset':
        return '时钟有固定偏差（见采集同步），要先平移时间才能评价对齐；本项暂不判定'
    clock = next((t for t in texts if '组间最多相差约' in t), None)
    if clock:
        gap = re.search(r'组间最多相差约 ([\d.]+) ?(秒|毫秒)', clock)
        text = f'相机和机器人本体两组设备的时钟没对准，相差约 {gap.group(1)} {gap.group(2)}' if gap else clock
        status = salvage_of(item)
        if status == 'fixed_offset':
            return text + '；但从头到尾一直差这么多（固定偏差），程序已把时间挪齐，其它检查都按挪齐后的时间做了。数据能用，要请采集方把时钟对准'
        moving = next((t for t in texts if t.startswith('不是固定偏差')), None)
        if algo == 'Check_Timestamp':
            # Same cause as Sync_Check, but say what it means for pairing frames with actions.
            apart = [s for s in item.get('sub_indicators') or []
                     if (m := re.search(r'IoU=([\d.]+)', str(s.get('raw_value', '')))) and float(m.group(1)) == 0]
            return (f'画面和动作配不上：{text}，而且这个差在数据中途还在变，没法挪齐（详见采集同步）'
                    + (f'；结果有 {len(apart)} 路和头部相机在时间上完全不重合，记 0 分' if apart else ''))
        return text + (f'；{moving}' if status == 'unstable' and moving else '，跨组时间无法比较')
    if algo == 'Check_Timestamp' and item.get('shared_row_intervals'):
        rows = item['shared_row_intervals']
        gaps = [e for e in rows['events'] if e['type'] == 'gap']
        back = [e for e in rows['events'] if e['type'] != 'gap']
        if not rows['events']:
            return ''
        listed = '、'.join(f"{clock_time(e['time_s'])}（空 {e['interval_s']:.2f} 秒）"
                          for e in sorted(gaps, key=lambda e: -e['interval_s'])[:3])
        return ('所有数据和画面共用一条时间（导出时已逐帧对齐），只查有没有跳帧：'
                + (f"{len(gaps)} 处空档（相邻两行相隔超过 {rows['max_gap_s']:g} 秒），最长的：{listed}" if gaps else '')
                + (f"；{len(back)} 处时间不前进" if back else '') + '。空档处画面和动作一起跳过')
    if algo == 'Check_Timestamp':
        topics, seen = [], set()
        for sc, name, why in sorted((float(m.group(2)), m.group(1), m.group(3)) for t in texts
                                    if (m := re.match(r'topic (\S+)：score=([\d.]+)，(.*)', t))):
            if (round(sc, 1), why) not in seen:  # Channels split from one message repeat its result.
                seen.add((round(sc, 1), why))
                topics.append((sc, name, why))
        mean = next((m for t in texts if (m := re.search(r'共评价 (\d+) 个 topic，算术平均分为 ([\d.]+)', t))), None)
        # The verdict comes from the mean over all topics, not from the worst one.
        reference = next((m.group(1) for t in texts if (m := re.search(r'reference topic 为 ([^\s。，]+)', t))), None)
        ref = names.get(reference, reference) if reference else '对照相机'
        head = (f'每一路都拿来和{ref}对比打分，{mean.group(1)} 路平均 {float(mean.group(2)):.1f} 分（高于 90 通过，80–90 复核，80 及以下排除）。'
                if mean else '')
        if topics:
            reference = reference or '对照相机'
            return head + '分数最低的：' + ''.join(timing_sentence(item, name, sc, reference, why, names)
                                              for sc, name, why in topics[:2])
        return head + '没有单独点名出问题的话题' if head else ''
    if algo == 'Root_Cause':
        subs = {s.get('name'): s for s in item.get('sub_indicators') or []}
        parts = [f"{s['raw_value']}（该子项 {s['score']:.0f} 分）" for name in ('跨通道Stall', '周期性丢帧')
                 if (s := subs.get(name)) and s.get('score') is not None and s['score'] < 100]
        unknown = [name for name in ('时钟漂移', '时钟跳变') if name in subs and subs[name].get('score') is None]
        short = item.get('short_input')
        if unknown and short:
            ok = [name for name in ('跨通道Stall', '周期性丢帧') if (subs.get(name) or {}).get('score') == 100]
            plain = {'跨通道Stall': '多路同时断档', '周期性丢帧': '周期性丢帧'}
            return (f"数据太短，{'、'.join(unknown)}算不了：整条只有 {short['seconds']:.1f} 秒，{names.get(short['channel'], short['channel'] + ' ')}只有 "
                    f"{short['samples']} 个样本，算法要求每一路至少 {short['required']} 个"
                    + (f"；另外两项（{'、'.join(plain[n] for n in ok)}）正常" if len(ok) == 2 else '')) + '；'.join([''] + parts)
        if unknown:
            parts.append('、'.join(unknown) + '没有评估：没有拿到时钟稳定性数据')
        return '；'.join(parts)
    if algo == 'Numeric_Check':
        stuck = [e for c in item.get('review_candidates') or [] for e in c.get('events') or []
                 if e.get('type') == 'joint_stuck_while_pose_moves']
        if stuck:
            stuck.sort(key=lambda e: -e['duration_s'])
            arm = SIDE.get(item.get('input_channel', '').split('/')[2] if item.get('input_channel', '').count('/') >= 3 else '', '手臂')
            shown = '；'.join(f"{clock_time(e['start_time_s'])} 起 {e['duration_s']:.1f} 秒读数不变，其中 "
                             f"{e.get('pose_moving_s', e['duration_s']):.1f} 秒末端确实在动（最远 {e['pose_move_m'] * 100:.0f} cm）"
                             + ('，一直到数据结尾' if e.get('reaches_end') else '') for e in stuck[:3])
            return (f'{arm}关节读数卡住：6 个关节的读数一个都没变，但{arm}末端在动（{shown}）。'
                    '这段时间关节数据没有更新，末端位置、姿态和画面是对的。可救：用逆运动学从末端位姿反算出这段关节角；'
                    '恢复之前，用关节角训练时这段关节角是错的，只用末端位姿训练时不受影响')
        events = [e for c in item.get('review_candidates') or [] for e in c.get('events') or []
                  if e.get('type') == 'joint_velocity_over_limit']
        if events:
            events.sort(key=lambda e: -abs(e['peak_value']))
            shown = '；'.join(f"第 {e['component_index'] + 1} 关节"
                             + (f" {clock_time(e['start_time_s'])} 起 {e['duration_s']:.2f} 秒" if e.get('start_time_s') else '')
                             + f"，峰值 {abs(e['peak_value']):.2f}"
                             + (f"（上限 {e['limit']:g}）" if len({x['limit'] for x in events}) > 1 else '') for e in events[:3])
            more = f'（另有 {len(events) - 3} 段）' if len(events) > 3 else ''
            limits = sorted({e['limit'] for e in events})
            return (f"{len(events)} 段速度超过 {limits[0]:g} rad/s：" if len(limits) == 1 else
                    f"{len(events)} 段速度超过本关节上限（rad/s）：") + f"{shown}{more}"
        frozen = [e for c in item.get('review_candidates') or [] for e in c.get('events') or []
                  if e.get('type') == 'constant_after_motion_candidate']
        if frozen:
            frozen.sort(key=lambda e: -(e.get('duration_s') or 0))
            part = '分量' if item.get('input_channel', '').startswith('imu') else '关节'
            shown = '；'.join(f"第 {e['component_index'] + 1} {part}"
                             + (f" {clock_time(e['start_time_s'])} 起 {e['duration_s'] * 1000:.0f} 毫秒" if e.get('start_time_s') else '')
                             + f"（{e['sample_count']} 个样本）" for e in frozen[:3])
            more = f'（另有 {len(frozen) - 3} 处）' if len(frozen) > 3 else ''
            return f'{len(frozen)} 处“动过之后数值连续不变”（可能是冻结，也可能只是正常停住）：{shown}{more}'
    if algo == 'Action_Smoothness' and item.get('score') is not None:
        jerk = ((item.get('raw') or {}).get('jerk') or {})
        sub = next((x for x in item.get('sub_indicators') or [] if x.get('name') == '轨迹抖动'), {})
        details = jerk.get('details') or {}
        if jerk.get('score') is not None and sub:
            arm = SIDE.get(item.get('side'), '手臂')
            frames = details.get('stationary_frames')
            share = f"（共 {frames} 帧，占 {frames / details['n_frames']:.0%}）" if frames and details.get('n_frames') else ''
            if details.get('jerk_frames') == 'slow_only' and details.get('method_jerk_score') is not None:
                share += f"（只算真正慢下来的帧；按原算法补帧后是 {details['method_jerk_score']:.1f}）"
            return (f"{arm}停住的时段{share}手的位置还在细微抖动：抖动指标 {jerk['score']:.1f}"
                    f"（{sub['t_max']:g} 及以下满分，{sub['t_min']:g} 及以上 0 分），平滑度 {item['score']:.0f} 分（85 分以下复核）")
    if algo == 'Video_Integrity':
        return frame_counts(item) or '；'.join((item.get('frame_quality') or {}).get('reasons') or [])
    if algo == 'Reset_Detection':
        tolerance = (item.get('calibration') or {}).get('tolerance')
        metres = (item.get('calibration') or {}).get('unit') == 'm'  # Tip position instead of joint angles.
        def where(e, key):
            """'，第 2 关节差约 69°，时刻 15:12:20.13' (or '，y 方向差约 6 cm') where the adapter recorded it."""
            if e.get(f'{key}_joint_index') is None:
                return ''
            time = e.get(f'{key}_time_s')
            index, offset = e[f'{key}_joint_index'], e[f'{key}_joint_offset']
            return ((f"，{'xyz'[index] if index < 3 else index + 1} 方向差约 {abs(offset) * 100:.0f} cm" if metres else
                     f"，第 {index + 1} 关节差约 {abs(math.degrees(offset)):.0f}°")
                    + (f'，时刻 {clock_time(time)}' if time else ''))
        def side(channel):
            parts = channel.split('/')
            key = parts[2] if len(parts) >= 3 else next((k for k in SIDE if k in channel), '')
            return SIDE.get(key, channel)
        def amount(value):
            return f'{value * 100:.1f} cm' if metres else f'{value:.2f} rad'
        guess = {'start': '疑似不是从初始位置开始录包', 'end': '疑似录包结束时没有回到初始位置'}
        out = [f"{side(e['channel'])}{label}和初始位置不同"
               f"（{'末端' if metres else ''}偏离 {amount(e[f'{key}_error'])}{where(e, key)}），{guess[key]}"
               for e in item.get('evidence', []) if e.get('channel')
               for key, label in (('start', '开头'), ('end', '结尾'))
               if key in item.get('checked_ends', ('start', 'end'))  # A cut-out segment: only real recording ends.
               and tolerance is not None and e.get(f'{key}_error') is not None and e[f'{key}_error'] > tolerance]
        return '；'.join(out)
    return next((t for t in findings(item) if not t.startswith(BOILERPLATE)), '')


def frame_counts(item):
    """'抽检 173 帧（每 5 帧抽 1）中：模糊 16 帧' from the frame check, or '' if no bad frame."""
    quality = item.get('frame_quality') or {}
    total = quality.get('total_frames') or 0
    bad = [f"{QUALITY[k]} {round(quality[k] * total)} 帧" if total and k != 'freeze_ratio'
           else f"{QUALITY[k]} {quality.get('freeze_frames', 0)} 帧" if k == 'freeze_ratio' else f'{QUALITY[k]} {quality[k]:.1%}'
           for k in QUALITY if (quality.get(k) or 0) > 0]
    step = (quality.get('sampling') or {}).get('sample_step')
    head = f'抽检 {total} 帧（每 {step} 帧抽 1）中：' if total and step else '画面问题帧：'
    return head + '，'.join(bad) if bad else ''


def timing_sentence(item, name, score, reference, labels, names):
    """One plain sentence per topic from what Check_Timestamp recorded; names are per-robot labels."""
    label = names.get(name, name)
    called = f'{label}（{name}）' if label != name else name
    ref = names.get(reference, reference)
    sub = next((s for s in item.get('sub_indicators') or [] if s.get('name') == name), {})
    atom = {d.get('name'): d for d in sub.get('details') or []}
    raw = sub.get('raw_value', '')
    iou = re.search(r'IoU=([\d.]+)', raw)
    rows = {c.get('channel'): c for c in (item.get('cadence') or {}).get('channels') or []}
    ms = lambda row, key: row[key] * 1000 if row.get(key) is not None else None
    row = rows.get(name, {})
    low, high, median = ms(row, 'min_interval_s'), ms(row, 'max_interval_s'), ms(row, 'median_positive_interval_s')
    frame = ms(rows.get(reference, {}), 'median_positive_interval_s')
    labels = [x for x in labels.split('；') if x]
    parts = []
    if '采集时间范围或采样周期不可评价' in labels:
        parts.append(f'{label}和{ref}的录制时间段只重合 {float(iou.group(1)):.0%}（算法要求至少 95%），不够就直接记 0 分，计入平均分'
                     if iou else f'{label}的录制时间段或采样周期没法评价，直接记 0 分，计入平均分')
    unit = '一帧' if any('相机流' in x for x in labels) or (median or 0) > 25 else '一条'
    if median and low is not None and high is not None and any(x in labels for x in ('动作流采样不稳定', '相机流采样不稳定', '动作流局部中断')):
        text = f'本该每 {median:.0f} 毫秒发{unit}，实际间隔在 {low:.1f} 到 {high:.1f} 毫秒之间变化'
        missing = round(high / median) - 1
        if missing >= 1:
            text += f'，最长一次隔了 {high:.1f} 毫秒（中间约少了 {missing} {unit[1:]}）'
        parts.append(text)
    p99 = atom.get('P99近邻误差') or {}
    if any(x in labels for x in ('P99 尾部误差偏大', 'P50 对齐误差偏大')) and p99.get('val') is not None:
        text = (f'{label}和{ref}逐帧配对时，最差的 1% 帧相差 {p99["val"]:.1f} 毫秒' if unit == '一帧' else
                f'因此给画面配{label}时，最差的 1% 画面要差 {p99["val"]:.1f} 毫秒才能找到{label}')
        if p99.get('t_min') is not None:
            text += f'（算法要求 {p99["t_min"]:.1f} 毫秒以内才不扣分）'
        if frame:
            text += (f'。{ref}每 {frame:.0f} 毫秒一帧，这点误差对配对影响很小' if p99['val'] < frame / 2 else
                     f'。{ref}每 {frame:.0f} 毫秒一帧，这个误差已接近或超过半帧，{ref}和{label}可能错开一帧')
        parts.append(text)
    if '参考帧匹配覆盖率不足' in labels and (atom.get('匹配覆盖率') or {}).get('val') is not None:
        parts.append(f'{ref}只有 {atom["匹配覆盖率"]["val"]:.1f}% 的帧找到了对应的{label}')
    for key, text in (('固定延迟', f'{label}的时间比{ref}整体早或晚一截'),
                      ('基准时间偏移', f'{label}的时间相对{ref}整体有偏移'),
                      ('时钟漂移', f'{label}的时间相对{ref}越走越偏')):
        if key in labels:
            offset = (atom.get('系统偏移') or {}).get('val')
            parts.append(text + (f'（约 {abs(offset):.1f} 毫秒）' if key != '时钟漂移' and offset is not None else ''))
    known = {'采集时间范围或采样周期不可评价', '动作流采样不稳定', '相机流采样不稳定', '动作流局部中断', 'P99 尾部误差偏大',
             'P50 对齐误差偏大', '参考帧匹配覆盖率不足', '固定延迟', '基准时间偏移', '时钟漂移', '跨流抖动'}
    parts += [x for x in labels if x not in known]
    return f'{called}和{ref}对比 {score:.0f} 分：' + '；'.join(parts) + '。'


def where(algo, item):
    if algo == 'Video_Integrity':
        return item.get('input_channel') or ''
    if algo == 'Numeric_Check' and item.get('input_field'):  # One channel can carry several fields (IMU).
        return f"{item.get('input_channel', '')} {item['input_field']}"
    return SIDE.get(item.get('side'), item.get('side')) or item.get('input_channel') or item.get('channel') or ''


def cell(algo, records, names=None):
    """{worst, note, problems: [[where, verdict, reason]]} for one algorithm of one episode."""
    if not records:
        return {'worst': 'missing', 'note': '没有记录', 'problems': []}
    failed = [r for r in records if r['status'] != 'completed']
    if failed:
        return {'worst': 'missing', 'note': failed[0]['status'], 'reason': str(failed[0].get('reason') or ''),
                'problems': [['', 'missing', str(failed[0].get('reason') or '算法没有完成')]]}
    items = [x for r in records for x in (reports(r) or [r.get('result') or {}])]
    verdicts = [verdict_of(x) for x in items]
    worst = min(verdicts, key=lambda v: ORDER.index(v) if v in ORDER else len(ORDER))
    problems = [[where(algo, x), verdict_of(x), explain(algo, x, names)] for x in items
                if verdict_of(x) in ('DO_NOT_USE', 'SALVAGE', 'NEEDS_REVIEW', 'OBSERVATION')]
    if algo == 'Action_Smoothness':
        note = ' · '.join(f"{SIDE.get(x.get('side'), '')}{LEVEL.get(verdict_of(x), (verdict_of(x),))[0]}" for x in items)
    elif len(items) > 2:
        counts = Counter(LEVEL.get(v, (v,))[0] for v in verdicts)
        note = f'{len(items)} 项：' + ' '.join(f'{k}{n}' for k, n in counts.most_common())
    else:
        note = ''
    if worst == 'SKIPPED':
        note = ''
    if any(frames_only(x) for x in items):
        note += ('（' if note else '') + '逐帧图片，无视频容器可查' + ('）' if note else '')
    salvage = next((s for x in items if (s := salvage_of(x))), None)
    clock = re.search(r'相差约 ([\d.]+) (秒|毫秒)', ' '.join(p[2] for p in problems))
    if algo == 'Video_Integrity':
        flagged = [f"{(where(algo, x).split('/') + ['', ''])[1] or where(algo, x)} {frame_counts(x)}" for x in items
                   if verdict_of(x) == 'ACCEPTABLE' and frame_counts(x)]
        if flagged:
            note += '；通过但有问题帧：' + '；'.join(flagged)
    if any(x.get('precondition') == 'clock_fixed_offset' for x in items):
        note = '等时钟平移后再评'
    elif any(x.get('clock_shift_s') for x in items):
        note = '已平移时钟后评价'
    elif clock:
        note = f'时钟差约 {clock.group(1)} {clock.group(2)}' + {'fixed_offset': '，可救', 'unstable': '，不能简单平移'}.get(salvage, '')
    short = next((x['short_input'] for x in items if x.get('short_input') and verdict_of(x) == 'ACCEPTABLE'), None)
    if algo == 'Root_Cause' and short:
        note += ('；' if note else '') + f"数据只有 {short['seconds']:.1f} 秒，漂移和跳变不需要查"
    if algo == 'Sync_Check' and salvage == 'fixed_offset' and worst == 'DO_NOT_USE':
        worst = 'SALVAGE'
        problems = [[w, 'SALVAGE' if v == 'DO_NOT_USE' else v, r] for w, v, r in problems]
    if algo == 'Check_Timestamp' and any(x.get('shared_row_intervals') for x in items):
        rows = next(x['shared_row_intervals'] for x in items if x.get('shared_row_intervals'))
        note = (f"共用一条时间，{rows['event_count']} 处跳帧" if rows['event_count'] else '共用一条时间，没有跳帧')
    segment = next((x for x in items if x.get('split_segment')), None)
    if algo == 'Reset_Detection' and segment:
        cut = segment['split_segment']
        note = (f"切片（原始第 {cut.get('start_frame')}–{cut.get('end_frame')} 帧），"
                + ('只查开头' if segment.get('checked_ends') else '不查复位'))
    return {'worst': worst, 'note': note, 'problems': problems, 'salvage': salvage,
            'shifted': any(x.get('clock_shift_s') for x in items),
            'complete': all(report_complete(x, algo) for x in items),
            'shared_rows': any(x.get('shared_row_intervals') for x in items)}


def summarize(output, names=None):
    """Small summary of one single-episode `edc run` output folder, or None if it has no run."""
    runs = sorted(Path(output).glob('_runs/*/'))
    if not runs or not (runs[-1] / 'selection.json').exists():
        return None
    run = runs[-1]
    selection = json.loads((run / 'selection.json').read_text(encoding='utf-8'))[0]
    algos = {}
    for algo in ALGOS:
        path = run / algo / 'records.jsonl'
        records = ([json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x]
                   if path.exists() else [])  # Action_Smoothness writes one record per arm.
        algos[algo] = cell(algo, records, names)
    short = any(c['worst'] == 'SKIPPED' for c in algos.values())
    if short or (algos.get('Check_Timestamp') or {}).get('shared_rows'):
        # One stored row time for every channel and video: there is no separate channel time to compare.
        # The framework does not even start these two, so a too-short episode shows them as not checked.
        for algo in ('Sync_Check', 'Root_Cause'):
            if algos[algo]['worst'] == 'missing' and '公共行时间' in algos[algo].get('reason', ''):
                algos[algo] = ({'worst': 'SKIPPED', 'note': '', 'problems': [], 'complete': True} if short else
                               {'worst': 'NA', 'note': '共用一条时间，无需检查', 'problems': [], 'complete': True})
    return {'decision': decision(selection['decision'], algos), 'algos': algos}


def decision(framework, algos):
    """The framework's decision, except an exclusion caused only by a fixed clock offset is shown as 可救, and
    checks this format cannot have (不适用) do not leave the episode 未评估完."""
    if framework == 'unassessed' and any(c['worst'] in ('NA', 'SKIPPED') for c in algos.values()):
        rest = [c for c in algos.values() if c['worst'] != 'NA']
        if all(c.get('complete') and c['worst'] in {'ACCEPTABLE', 'NEEDS_REVIEW', 'SALVAGE', 'DO_NOT_USE', 'SKIPPED'}
               for c in rest):
            worst = {c['worst'] for c in rest}
            framework = ('exclude' if worst & {'DO_NOT_USE', 'SALVAGE'} else 'review' if 'NEEDS_REVIEW' in worst
                         else 'keep')
    # After the shift, a Check_Timestamp exclusion is about alignment itself, not the clock.
    excluded = {a for a, c in algos.items() if c['worst'] == 'DO_NOT_USE' and not c.get('shifted')}
    if any(c['worst'] == 'DO_NOT_USE' and c.get('shifted') for c in algos.values()):
        return framework
    fixed = (algos.get('Sync_Check') or {}).get('salvage') == 'fixed_offset'
    # 可救 when every exclusion has a known fix: a fixed clock offset, or stuck joint readings (IK from the tip pose).
    fixable = not excluded or fixed and excluded <= CLOCK_ALGOS
    return 'salvage' if framework == 'exclude' and fixable else framework


def chip(verdict):
    word, tone, meaning = LEVEL.get(verdict, (str(verdict), 'na', ''))
    return f'<span class="chip {tone}" title="{esc(meaning)}">{esc(word)}</span>'


def page(bags, info):
    rank = list(DECISION)
    bags = sorted(bags, key=lambda b: (rank.index(b['decision']) if b['decision'] in rank else 9, b['name']))
    decisions = Counter(b['decision'] for b in bags)
    cards = ''.join(f'<div class="card {DECISION[d][1]}"><strong>{decisions[d]}</strong>{DECISION[d][0]}'
                    f'<small>{DECISION[d][2]}</small></div>' for d in DECISION if decisions[d])
    legend = ''.join(f'<li>{chip(v)} {esc(LEVEL[v][2])}</li>'
                     for v in ('ACCEPTABLE', 'NEEDS_REVIEW', 'SALVAGE', 'DO_NOT_USE'))
    head = ''.join(f'<th>{esc(NAMES.get(a, a))}</th>' for a in ALGOS)
    rows, details, issues = [], [], {a: [] for a in ALGOS}
    for i, b in enumerate(bags):
        word, tone, _ = DECISION.get(b['decision'], (b['decision'], 'na', ''))
        name = f'<a href="#bag{i}">{esc(b["name"])}</a> <span class="chip {tone}">{esc(word)}</span>'
        tds, lines = [], []
        for algo in ALGOS:
            c = b['algos'].get(algo)
            if c is None:
                tds.append('<td></td>')
                continue
            tip = '\n'.join(f'{w}：{r}' for w, v, r in c['problems'])
            tds.append(f'<td title="{esc(tip)}">{chip(c["worst"])}<small>{esc(c["note"])}</small></td>')
            if c['worst'] in ('DO_NOT_USE', 'SALVAGE', 'NEEDS_REVIEW', 'missing'):
                issues[algo].append((i, b, c))
            reasons = ''.join(f'<li>{chip(v)} <b>{esc(w)}</b> {esc(r)}</li>' for w, v, r in c['problems'])
            lines.append(f'<tr><td>{esc(NAMES.get(algo, algo))}</td><td>{chip(c["worst"])} '
                         f'<span class="muted">{esc(c["note"])}</span>' + (f'<ul>{reasons}</ul>' if reasons else '') + '</td></tr>')
        rows.append(f'<tr><th>{name}</th>' + ''.join(tds) + '</tr>')
        extra = (f'<pre>{esc(b.get("log", ""))}</pre>' if b['decision'] == 'failed' else
                 f'<table class="detail">{"".join(lines)}</table>')
        kept = f'<p class="muted">完整框架输出（含单条报告和视频证据）：{esc(b["kept"])}</p>' if b.get('kept') else ''
        details.append(f'<details id="bag{i}"><summary>{esc(b["name"])} <span class="chip {tone}">{esc(word)}</span> '
                       f'<span class="muted">{b["size_mb"]} MB · 用时 {b["seconds"]:.0f} 秒</span></summary>'
                       f'<p class="muted">{esc(b["source"])}</p>{kept}{extra}</details>')
    sections = []
    for algo in ALGOS:
        found = issues[algo]
        tally = Counter(LEVEL[c['worst']][0] for _, _, c in found)
        summary = '，'.join(f'{k} {n} 条' for k, n in tally.items()) or '没有排除、可救或复核项'
        body = ''.join(f'<li><a href="#bag{i}">{esc(b["name"])}</a> {chip(c["worst"])} <span class="muted">{esc(c["note"])}</span><ul>'
                       + ''.join(f'<li><b>{esc(w)}</b> {esc(r)}</li>' for w, v, r in c['problems']
                                 if v in ('DO_NOT_USE', 'SALVAGE', 'NEEDS_REVIEW', 'missing')) + '</ul></li>'
                       for i, b, c in found)
        guide = f'<p class="guide">怎么复核：{esc(GUIDE[algo])}</p>' if found and algo in GUIDE else ''
        sections.append(f'<details><summary>{esc(NAMES.get(algo, algo))} <span class="muted">{summary}</span></summary>'
                        f'{guide}<ul>{body}</ul></details>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>批量质检报告 · {esc(info['robot'])}</title><style>
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
<h1>批量质检报告 · {esc(info['robot'])}</h1>
<p class="muted">{esc(info['inputs'])} · {len(bags)} 条 · {esc(info['finished'])} 完成，用时 {info['seconds']:.0f} 秒（同时 {info['jobs']} 条）<br>
配置 {esc(info['profile'])} · 参数 {esc(info['params'] or '无')}<br>
每条都由框架 <code>edc run</code> 跑完全部 9 个算法；本页只汇总算法写下的结论和原因，不计算新分数、不做总分。</p>
<div class="box"><b>结论怎么读</b><ul>{legend}</ul>
<span class="muted">一个算法常有多个输出（例如数值检查每个字段一项、视频每路相机一项），表格格子里显示其中最差的一个，下方小字是数量。
整条的结论：有一项“排除”就是“含建议排除项”；没有排除、但采集同步是“可救”的，整条就是“可救”；
都没有、但有“复核”就是“建议复核”；全部通过才是“建议保留”。</span></div>
<h2>整体结论</h2><div class="cards">{cards}</div>
<h2>逐条一览 <span class="muted">点名字看该条详情，鼠标停在格子上看原因</span></h2>
<div class="scroll"><table><thead><tr><th>数据</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>按算法看问题 <span class="muted">只列排除和复核</span></h2>{''.join(sections)}
<h2>逐条详情</h2>{''.join(details)}
</main></body></html>'''
