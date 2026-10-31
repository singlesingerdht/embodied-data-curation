"""Readable offline presentation of recorded results; never recomputes judgments."""
from collections import Counter, OrderedDict
import html
import json
from pathlib import Path
from urllib.parse import quote

NAMES = {
    'Structure_Check': '结构完整性', 'Numeric_Check': '数值检查',
    'Action_Smoothness': '动作平滑度', 'Check_Timestamp': '时间戳对齐',
    'Sync_Check': '采集同步', 'Root_Cause': '时间异常归因',
    'Dedup_Check': '时间／序列重复检查', 'Reset_Detection': '复位检测',
    'Video_Integrity': '视频完整性',
    'Visual_Quality': '视觉质量', 'Projection_Alignment': '位姿投影对齐',
}
STATUSES = {'completed': '已完成', 'missing_input': '缺少输入',
            'needs_calibration': '需要标定', 'missing_dependency': '缺少依赖',
            'processing_error': '执行失败'}
VERDICTS = {'ACCEPTABLE': '该项可接受', 'NEEDS_REVIEW': '建议复核',
            'DO_NOT_USE': '该项建议排除', 'NOT_APPLICABLE': '不适用',
            'OBSERVATION': '仅供参考'}
CAPABILITIES = {'pose': '可用的末端位姿', 'timestamps': '已确认的时间轴',
                'multi_timestamps': '可比较的多通道时间轴', 'sync': '同一时钟下的独立采集时间轴',
                'joint': '可用的关节位置', 'numeric': '可读取的数值字段', 'video': '可解码的图像',
                'dedup_values': '可比较的原始时间值'}


def esc(value):
    return html.escape(str(value), quote=True)


def score(value):
    if value is None:
        return '—'
    return esc(f'{value:g}' if type(value) in (int, float) else value)


def safe_path(root, ref):
    path = (root / ref).resolve()
    if Path(ref).is_absolute() or not path.is_relative_to(root):
        raise ValueError('Report reference escapes output')
    return path


def read_json(root, ref):
    return json.loads(safe_path(root, ref).read_text(encoding='utf-8')) if ref else {}


def reason_text(record):
    reason = record.get('reason', '')
    if reason.startswith('Missing capabilities: '):
        missing = reason.removeprefix('Missing capabilities: ').split(', ')
        return '缺少' + '、'.join(CAPABILITIES.get(x, x) for x in missing) + '，本项未评估。'
    if reason.startswith('Embedded video requires explicit FPS'):
        return '图像可以解码，但缺少已确认的帧率或时间轴，无法完成视频检测。'
    if reason == 'Provide algorithm-specific calibration':
        return '缺少该算法所需的标定参数；复位检测需提供参考关节位置和容差。'
    if reason == 'Independent acquisition timelines are required':
        return '缺少独立采集时间轴，不能用共用或合成时间证明采集同步。'
    if 'Finiteness checks available; physical checks configured for ' in reason:
        scope = reason.split('configured for ', 1)[1].split(' fields.', 1)[0]
        return f'可检查 NaN/Inf；{scope} 个字段满足物理检查前置条件。未知单位或物理量不进入物理检测。'
    if 'physical quantity is unconfirmed' in reason:
        return '物理量尚未确认，请使用者说明字段含义与实际单位。原始原因：' + reason
    if 'Unknown/unsupported source unit' in reason:
        return '源单位为空、未知或尚不支持，无法执行依赖该单位的物理检查。原始原因：' + reason
    if 'physical dimensions do not match' in reason:
        return '当前声明的物理量纲与算法要求不同，不能通过单位换算替代。原始原因：' + reason
    return reason or '尚无可显示的评估结论。'


def reports(record):
    items = (record.get('result') or {}).get('reports', [])
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def findings(item):
    result = []
    if item.get('supplemental_verdict') == 'DO_NOT_USE':
        result.append('动作指令超出已确认的独立限位：筛选建议排除，原算法评分保留。')
    if any(candidate.get('check') in {'joint_freeze', 'cmd_freeze', 'gyro_freeze', 'accel_freeze'}
           for candidate in item.get('review_candidates', [])):
        result.append('发现运动后恒值区间，可能为正常保持或数值冻结，需要复核；不据此确认采集故障。')
    if item.get('inspection_object') == 'derived_preview':
        result.append('检查对象为派生预览；该结果不能证明原始 JPEG／视频编码完整性。')
    elif item.get('inspection_object') == 'original_encoded_video':
        result.append('检查对象为保留的原始编码视频；帧区间与来源映射另存。')
    for key in ('reasons', 'warnings'):
        values = item.get(key) or []
        result.extend(str(x) for x in (values if isinstance(values, list) else [values]))
    if item.get('reason'):
        explanations = {
            'Physical thresholds not applied: Numeric configuration confirmation, source unit or calibration pending':
                '已读取字段未发现 NaN/Inf；单位、标定或参数尚未确认，未执行物理阈值检查。',
            'Nonfinite numeric values': '字段含 NaN 或 Inf，请查看逐字段计数和异常位置。',
            'Empty numeric field': '数值字段为空，本字段未完成评估。',
        }
        result.append(explanations.get(str(item['reason']), str(item['reason'])))
    frame_quality = item.get('frame_quality')
    if isinstance(frame_quality, dict):
        for text in frame_quality.get('reasons') or []:
            result.append('画面质量子项：' + str(text))
        if frame_quality.get('verdict') in {'NEEDS_REVIEW', 'DO_NOT_USE'}:
            result.append('画面质量子项要求复核或排除，请勿仅按顶层评分判断。')
    structure = item.get('structure')
    structure = structure if isinstance(structure, dict) else {}
    pairs = structure.get('camera_pairs')
    pairs = pairs if isinstance(pairs, dict) else {}
    if pairs.get('orphan_images'):
        result.append(f"{len(pairs['orphan_images'])} 路相机未找到配对的 camera_info。")
    topics = structure.get('topics')
    topics = topics if isinstance(topics, dict) else {}
    if topics.get('missing_required'):
        result.append('缺少必需通道：' + ', '.join(topics['missing_required']))
    if topics.get('empty_topics'):
        result.append('空通道：' + ', '.join(topics['empty_topics']))
    cameras = structure.get('camera_configuration') or {}
    if cameras and not cameras.get('evaluated'):
        result.append(cameras.get('reason', '相机要求未配置，未评估'))
    for check in cameras.get('checks', []):
        if check['status'] != 'present':
            result.append(f"相机 {check['sensor_id']} 所需 {check['modality']}：{check['status']}")
    td = (item.get('dedup') or {}).get('timestamp_dedup') or {}
    for blocked in item.get('unavailable_inputs', []):
        result.append(f"{blocked['channel']}：时间重复未评估，{blocked['reason']}")
    if 'extra_duplicate_count' in td:
        result.append(f"时间戳相同涉及 {td['involved_record_count']} 条记录，额外出现 {td['extra_duplicate_count']} 次；"
                      f"相邻相等 {td['adjacent_equal_count']} 次，非相邻复用 {td['nonadjacent_reuse_count']} 次。"
                      '这不证明内容复制；旧评分仅覆盖连续至少五条相同的片段。')
    cadence = item.get('cadence') or {}
    for row in cadence.get('channels', []):
        if row['status'] == 'missing_input':
            result.append(f"{row['channel']}：无法检查采集节奏，{row.get('reason', '')}")
        elif row.get('backward_interval_count') or row.get('zero_interval_count'):
            result.append(f"{row['channel']}：倒退 {row['backward_interval_count']} 个间隔，零间隔 {row['zero_interval_count']} 个")
    if item.get('cross_channel_alignment', {}).get('status') == 'missing_input':
        result.append('各路时间间隔已分别观察；跨路条件不足，不计算对齐得分或认定采集同步。')
    if structure.get('severity') in {'warning', 'error'} or structure.get('passed') is False:
        result.append('结构明细含警告或未通过项；请结合明细复核，不能只看评分。')
    return list(dict.fromkeys(result))


def brief(record):
    if record['status'] != 'completed':
        return esc(reason_text(record))
    items = reports(record)
    alerts = [text for item in items for text in findings(item)]
    if alerts:
        return esc('；'.join(dict.fromkeys(alerts)))
    verdicts = Counter(x.get('verdict') for x in items)
    if any(verdicts.get(v) for v in ('DO_NOT_USE', 'NEEDS_REVIEW')):
        return '发现需处理或复核的结果，请展开查看。'
    if items and all(x.get('verdict') == 'ACCEPTABLE' for x in items):
        return f'{len(items)} 项输出均判为可接受；仅覆盖已检查的字段和条件。'
    return '已执行；详细结果和证据见下方。'


def badge(status):
    tone = 'done' if status == 'completed' else 'error' if status == 'processing_error' else 'pending'
    return f'<span class="badge {tone}">{esc(STATUSES.get(status, status))}</span>'


def selection_note(selection):
    if not selection:
        return '尚无整体筛选结论。'
    decision = selection.get('decision')
    complete = selection.get('assessment_complete', False)
    meanings = {'keep': '当前所选检查建议保留', 'review': '当前结果建议复核',
                'exclude': '当前结果包含建议排除项', 'unassessed': '整体尚未评估完成'}
    if decision == 'keep' and not complete:
        return '记录中的保留建议与未完成状态矛盾，请复核原始记录。'
    suffix = '；评估不完整，不能据此认定整体合格。' if not complete else '；结论仅限本次所选算法。'
    return meanings.get(decision, str(decision)) + suffix


def configuration_panel(profile, params, normalization=None, recommendations=None):
    from edc.robots.numeric_review import numeric_review
    review = numeric_review(profile, params.get('Numeric_Check', {}))
    state = '已由 ' + review['confirmed_by'] + ' 确认' if review['status'] == 'confirmed' else '配置已变更，必须重新确认' if review['status'] == 'stale' else '尚未进行参数确认：请作者完成这一步'
    policy = (normalization or {}).get('policy')
    policy_note = ('自动换算已启用：明确声明且受支持的源单位在算法读取时换算为 SI；原始文件和源配置保持不变。'
                   if policy == 'auto' else '自动换算已关闭：调用方须自行准备 SI 数据并声明其真实单位；非 SI 或未知物理输入被阻止。'
                   if policy == 'manual' else '历史运行未记录自动换算策略；不能据此认定已完成单位适配。')
    units = '；'.join(f'{key}={value}' for key, value in review['unit_suggestions'].items())
    labels = {'joint': '关节反馈', 'cmd': '动作指令', 'imu': 'IMU', 'pose_freeze': '位姿冻结',
              'command_limits': '动作指令独立限位', 'joint_freeze': '关节反馈冻结候选',
              'cmd_freeze': '动作指令冻结候选', 'gyro_freeze': '角速度冻结候选',
              'accel_freeze': '加速度冻结候选', 'joint_velocity': '关节速度上限（适配层补充检查）'}
    descriptions = {'vel_threshold': '相邻样本差阈值（非每秒速度）', 'velocity_peak': '单步差峰值',
                    'reset_ratio': '大步差占比（非 Home 复位）', 'limits': '逐维范围',
                    'jerk_threshold': '二阶样本差阈值（非物理 jerk）', 'min_run': '最短冻结样本数',
                    'lookback': '回看样本数', 'freeze_eps': '冻结位移阈值',
                    'motion_threshold': '此前运动全幅差阈值（非速度）',
                    'gyro_max': '角速度幅值上限', 'acc_max': '加速度幅值上限',
                    'gyro_bias_max': '角速度偏置上限', 'acc_bias_max': '加速度偏置上限',
                    'min_static_frames': '最短静止样本数', 'max_abs': '记录速度绝对值上限'}

    def threshold_unit(group, key):
        # Joint thresholds are expressed in the field's SI unit. A joint-space
        # configuration may contain both rad (revolute) and m (prismatic) fields,
        # so the global threshold table must not claim one of them.
        if key in {'min_run', 'lookback', 'min_static_frames'}:
            return '样本'
        if key == 'reset_ratio':
            return '比例'
        if group in {'joint', 'cmd', 'joint_freeze', 'cmd_freeze', 'command_limits'}:
            if key == 'limits':
                return '字段单位'
            if group == 'cmd' or group == 'command_limits':
                return '字段单位/样本²' if group == 'cmd' else '字段单位'
            if key in {'freeze_eps', 'motion_threshold'}:
                return '字段单位'
            return '字段单位/样本'
        if group == 'gyro_freeze':
            return 'rad/s'
        if group == 'accel_freeze':
            return 'm/s²'
        if group == 'joint_velocity':
            return 'rad/s'
        if group == 'pose_freeze':
            return 'm' if key == 'freeze_eps' else 'm/样本'
        return 'rad/s' if key.startswith('gyro') else 'm/s²'

    rows = []
    for group, values in review['settings']['thresholds'].items():
        for key, value in values.items():
            value = '未设置，不检查该范围' if value is None else json.dumps(value, ensure_ascii=False)
            description = ('恒值区间全幅差阈值' if key == 'freeze_eps' and group != 'pose_freeze'
                           else descriptions[key])
            unit = threshold_unit(group, key)
            rows.append(f'<tr><td>{esc(labels[group])}</td><td>{esc(description)}<span class="muted block">{esc(key)}</span></td><td>{esc(value)}</td><td>{esc(unit)}</td></tr>')
    return ('<section class="report-section" data-section="configuration"><h3>参数与单位确认</h3>'
        f'<p class="notice">{esc(state)}。有限值检查始终执行；相关物理检查须完成确认。</p>'
        f'<p>默认建议单位系统：SI。{esc(units)}。</p><p>{esc(policy_note)}</p>'
        '<p class="muted">阈值始终按 SI 配置，不按源单位重新缩放。关闭自动换算：edc run ... --unit-policy manual；关闭不会授权算法读取非 SI 物理输入。</p>'
        '<p class="muted">建议值不是源数据单位声明；原始单位未知时保持未知。请确认字段物理量、指令/反馈、单位、校准与阈值。这里只展示本次配置及建议值。</p>'
        '<details class="subdetail"><summary>查看并调整数值检查阈值</summary><div class="scroll"><table><tr><th>类别</th><th>参数含义</th><th>配置值／默认建议</th><th>单位／基准</th></tr>'
        + ''.join(rows) + '</table></div>'
        '<p>编辑 robot.yaml 的 numeric_check.thresholds、字段 unit 和 calibration_status 后，由作者确认：</p>'
        '<pre>edc robot confirm-numeric --profile robot.yaml --reviewer "作者姓名" --output robot.confirmed.yaml</pre>'
        '<p>随后使用确认后的配置重跑。单位或阈值变更后需要重新确认；确认不等于数据质量通过。</p></details>'
        + recommendation_panel(recommendations) + '</section>')


def recommendation_panel(pool):
    parts = ['<h4>推荐算法池</h4><p class="muted">按本次记录的输入配置、单位、标定和依赖预检；可执行不表示已执行或数据合格。未知单位不送入物理算法，部分检查不表示整项达标。</p>']
    if not pool:
        return ''.join(parts) + '<p>历史运行未记录推荐池；不使用当前配置或环境追溯计算。</p>'
    labels = {'available': '可执行', 'partial': '仅部分检查', 'unavailable': '不可用'}
    for episode in pool.get('episodes', []):
        parts.append(f'<p>评估对象：{esc(episode.get("episode_id"))}</p>')
        if episode.get('error'):
            parts.append(f'<p>{esc(episode["error"])}</p>')
        parts.append('<div class="scroll"><table><tr><th>算法</th><th>可用范围</th><th>当前原因／待补配置</th><th>参数与输入要求</th></tr>')
        for item in episode.get('algorithms', []):
            ident = item['algorithm']['id']
            reason = reason_text(item) if item.get('reason') else '已满足已声明的前置条件，执行时仍需核验数据'
            details = []
            for field in item.get('fields', []):
                state = ('仅部分物理子项可用' if field.get('coverage_blockers') else '物理检查可用') if field.get('physical_checks_available') else '仅有限值检查'
                details.append(f'<li>{esc(field["channel"])}.{esc(field["field"])}：{esc(state)}；'
                    f'源 {esc(field.get("source_unit") or "未知")} → 执行 {esc(field.get("execution_unit") or "未知")}；'
                    + esc('；'.join(dict.fromkeys([*field.get('reasons', []), *field.get('coverage_blockers', [])]))) + '</li>')
            extra = '<details><summary>逐字段适用性</summary><ul>' + ''.join(details) + '</ul></details>' if details else ''
            contract = item.get('parameters') or {}
            description = ''.join('<li>' + esc(x) + '</li>' for x in item.get('input_descriptions', []))
            input_text = '<ul>' + description + '</ul>' if description else ''
            metadata = {
                '必需输入': [CAPABILITIES.get(x, x) for x in item.get('required_inputs', [])],
                '具体数据要求': item.get('data_requirements', item.get('input_requirements', [])),
                '满足情况': item.get('requirement_checks', []),
                '参数说明': contract.get('definitions', {}),
                '参数规则与默认值': contract.get('schema', {}),
                '本次生效参数': contract.get('effective', {}),
                '环境要求': item.get('environment_requirements', {}),
                '实际输入': item.get('inputs', []), '不可用输入': item.get('unavailable_inputs', [])}
            config = (input_text + '<details><summary>查看本次参数与输入契约</summary><pre>'
                      + esc(json.dumps(metadata, ensure_ascii=False, indent=2)) + '</pre></details>')
            parts.append(f'<tr><td>{esc(NAMES.get(ident, ident))}<span class="muted block">{esc(ident)}</span></td>'
                f'<td>{esc(labels.get(item.get("availability"), "未知"))}</td><td>{esc(reason)}{extra}</td><td>{config}</td></tr>')
        parts.append('</table></div>')
    datasets = pool.get('dataset_algorithms', [])
    if datasets:
        parts.append('<h4>数据集级算法</h4><p>由插件的 preflight_dataset 检查整体输入；可执行不代表已验证估值效果。</p>'
                     '<div class="scroll"><table><tr><th>算法</th><th>可用范围</th><th>原因／生效参数</th></tr>')
        for item in datasets:
            parts.append('<tr><td>' + esc(item['algorithm']['id']) + '</td><td>'
                         + esc(labels.get(item.get('availability'), '未知')) + '</td><td>'
                         + esc(item.get('reason') or '已满足插件声明的前置条件') + '<pre>'
                         + esc(json.dumps(item.get('effective_params'), ensure_ascii=False)) + '</pre></td></tr>')
        parts.append('</table></div>')
    if pool.get('discovery_errors'):
        parts.append('<details open><summary>插件发现失败（其他插件继续）</summary><pre>'
                     + esc(json.dumps(pool['discovery_errors'], ensure_ascii=False, indent=2)) + '</pre></details>')
    return ''.join(parts)


def numeric_panel(records, root):
    rows = []
    subchecks = []
    downloads = []
    states = {'evaluated': '已检查', 'not_applicable': '不适用', 'unconfigured': '未配置',
              'pending_confirmation': '待确认', 'insufficient_input': '缺少有效输入／区段',
              'blocked_nonfinite': '有限值异常，未执行物理项', 'unsupported': '当前未实现',
              'invalid_configuration': '配置与当前字段维度不匹配'}
    for record in records:
        if record['algorithm']['id'] != 'Numeric_Check':
            continue
        for ref in record.get('artifacts') or []:
            if ref.endswith('.csv'):
                safe_path(root, ref)
                downloads.append(f'<a href="{esc(quote(ref, safe="/"))}" download>完整异常位置 CSV · {esc(Path(ref).name)}</a>')
        for item in reports(record):
            examples = item.get('nonfinite_examples') or []
            positions = '；'.join(f"{x.get('kind')} @ {x.get('indices')}" for x in examples[:8])
            if not positions:
                shape = item.get('shape') or [None]
                positions = '未发现' if item.get('finite') is True else '空字段' if shape[0] == 0 else '旧记录未保存位置'
            if len(examples) > 8 or item.get('nonfinite_indices_truncated'):
                positions += '；更多位置见原始记录及完整 CSV'
            review = item.get('configuration_review') or {}
            state = '已确认并执行' if item.get('physical_checks_applied') else '待配置确认' if review.get('status') != 'confirmed' else '单位、标定或算法适用范围未满足'
            if item.get('physical_check_blockers'):
                state += '：' + '；'.join(item['physical_check_blockers'])
            label = str(item.get('input_channel', '未记录')) + ' · ' + str(item.get('input_field', '未记录'))
            coverage = item.get('check_coverage') or {}
            if not coverage:
                subchecks.append(f'<p>{esc(label)}：历史记录未保存分项覆盖，不据总分推算。</p>')
            else:
                checks = []
                for check in coverage.get('checks', []):
                    outcome = {'finding': '发现线索／异常', 'no_finding': '本项未发现异常',
                               'observation': '恒值观察，未判故障'}.get(check.get('outcome'), '未作判断')
                    checks.append('<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in (
                        check['name'], states.get(check['status'], check['status']), outcome,
                        json.dumps(check.get('threshold'), ensure_ascii=False) if check.get('threshold') is not None else '—',
                        check.get('unit') or '—', check.get('reason') or '—')) + '</tr>')
                subchecks.append('<details class="subdetail"><summary>' + esc(label) + ' · 分项检查覆盖</summary>'
                    '<div class="scroll"><table><tr><th>子项</th><th>执行状态</th><th>结果</th><th>阈值</th><th>单位／基准</th><th>说明</th></tr>'
                    + ''.join(checks) + '</table></div></details>')
            for check in item.get('supplemental_checks', []):
                events = check.get('events', []) + check.get('observations', [])
                if not events:
                    continue
                evidence = json.dumps({'source_mapping': check['source_mapping'],
                    'timeline': check.get('timeline'), 'events': events[:20]}, ensure_ascii=False, indent=2)
                event_rows = []
                for event in events[:20]:
                    interval = (f"[{event['start']}, {event['stop']})" if 'start' in event else event['source_row'])
                    duration = event.get('duration_s')
                    meaning = event.get('interpretation') or f"值 {event['value']:g}，允许 [{event['lower']:g}, {event['upper']:g}] rad"
                    event_rows.append('<tr>' + ''.join('<td>' + esc(value) + '</td>' for value in (
                        event['source_component_index'], interval,
                        f'{duration:g} s' if duration is not None else '—', meaning)) + '</tr>')
                subchecks.append('<details class="subdetail"><summary>' + esc(label + ' · ' + check['name'])
                    + (' · 末端在动但关节读数不变，可救：用末端位姿逆运动学恢复关节角（原评分保留）' if check['id'] == 'joint_stuck' and check.get('verdict') == 'DO_NOT_USE'
                       else ' · 指令越界，筛选排除（原评分保留）' if check.get('verdict') == 'DO_NOT_USE'
                       else f" · {check.get('segments')} 段超限，需复核" if check['id'] == 'joint_velocity_limit'
                       else ' · 区间证据，恒值不等于采集故障')
                    + '</summary><p>行号从 0 开始，区间 [start, stop)；持续时间仅来自当前通道有效记录时间。'
                    '预览最多 20 条，全部事件保存在原始记录及 CSV。</p><div class="scroll"><table>'
                    '<tr><th>源分量</th><th>源行／区间</th><th>记录时间跨度</th><th>说明</th></tr>'
                    + ''.join(event_rows) + '</table></div><details><summary>来源与原始证据</summary><pre>'
                    + esc(evidence) + '</pre></details></details>')
            source_unit = item.get('source_unit', item.get('unit')) or '未知'
            execution_unit = (item.get('unit') or '未知') if item.get('input_scope') == 'SI' else '未进入 SI 物理检测' if item.get('input_scope') == 'recorded_finiteness_only' else '旧记录未区分'
            conversion = (item.get('unit_normalization_reason') or ('已自动换算' if source_unit != item.get('unit') else '原始已为 SI')) if item.get('input_scope') else '历史记录未保存'
            rows.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in (
                label, item.get('shape', '未记录'), source_unit, execution_unit, conversion,
                item.get('suggested_unit') or '物理量未声明',
                item.get('nan_count', '未记录'), item.get('posinf_count', '未记录'),
                item.get('neginf_count', '未记录'), positions, state)) + '</tr>')
    return ('<section class="report-section" data-section="numeric-fields"><h3>逐字段 NaN / Inf 明细</h3>'
        '<p class="muted">NaN 是无效数值，+Inf / −Inf 是正负无穷大，均为数据中的数值异常。位置使用从 0 开始的 [样本, 分量] 索引；拆分视图保留源分量映射，重复视图不合计成新样本。</p>'
        + ('<div class="scroll"><table class="numeric-fields"><thead><tr><th>通道／字段</th><th>形状</th><th>源声明单位</th><th>算法执行单位</th><th>换算状态／阻塞原因</th><th>SI 建议单位</th><th>NaN</th><th>+Inf</th><th>−Inf</th><th>异常位置</th><th>物理检查</th></tr></thead><tbody>'
           + ''.join(rows) + '</tbody></table></div>' if rows else '<p class="muted">本次未保存数值字段检查结果。</p>')
        + ('<h4>数值检查覆盖明细</h4><p>原始分数及 assessment_complete 按算法返回值展示；质量放行同时核验分项覆盖。未配置的限位、缺少的联合输入或区段不会算作通过；联合 IMU 偏置线索单独复核，不改写原始评分。</p>'
           + ''.join(subchecks) if subchecks else '')
        + ('<div class="evidence">' + ''.join(downloads) + '</div>' if downloads else '') + '</section>')


def media_panel(root, ref):
    descriptor = read_json(root, ref) if ref else {}
    if ref and descriptor.get('schema_version') != 'dqs_presentation_v1':
        raise ValueError('Unsupported presentation evidence schema')
    channels = descriptor.get('channels') or []
    parts = ['<section class="report-section" id="media" data-section="media"><h2>视频与画面证据</h2>',
             '<p class="muted">播放时间来自原视频 PTS；不自动与数值行或采集时钟对齐。画面候选需要人工复核。</p>']
    def url(value):
        safe_path(root, value)
        return esc(quote(value, safe='/'))
    if not channels:
        parts.append('<p>本次没有附加可播放的视频证据；其余区域的版式保持一致。</p>')
    for index, item in enumerate(channels):
        ident = f'evidence-video-{index}'
        title = item.get('label') or item.get('channel') or f'视频 {index+1}'
        parts.append(f'<details class="media-channel" {"open" if index == 0 else ""}><summary>{esc(title)} · {esc(item.get("finding", "待复核画面"))}</summary><div class="media-viewer">'
            f'<div><video id="{ident}" controls preload="metadata" src="{url(item["video"])}"></video>'
            f'<p class="muted">源文件：{esc(item.get("source", "未记录"))}</p><div class="segments">')
        for segment in item.get('segments') or []:
            import math
            seconds = float(segment['start_playback_s'])
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError('Invalid evidence playback timestamp')
            parts.append(f'<button type="button" data-video="{ident}" data-seconds="{seconds}">{esc(segment["label"])}</button>')
        parts.append('</div><p class="seek-state" aria-live="polite"></p></div><div class="evidence-photos">')
        for key, label in (('candidate', '候选画面'), ('reference', '中位参考画面')):
            value = item.get(key)
            if value:
                parts.append(f'<figure><img loading="lazy" src="{url(value["image"])}" alt="{esc(title + label)}"><figcaption>{label} · 帧 {esc(value.get("frame"))} · {esc(value.get("playback_time_s"))} 秒</figcaption></figure>')
        parts.append('</div></div>')
        if item.get('report'):
            parts.append(f'<p><a href="{url(item["report"])}">查看该视频的原始算法报告</a></p>')
        parts.append('</details>')
    if descriptor.get('review_csv'):
        parts.append(f'<p><a href="{url(descriptor["review_csv"])}" download>下载逐帧复核清单 CSV</a></p>')
    if descriptor.get('note'):
        parts.append(f'<p class="notice">{esc(descriptor["note"])}</p>')
    return ''.join(parts) + '</section>'


def architecture_panel():
    stages = [('原始数据', 'Dataset 来源只读；HDF5、MP4、MCAP、Parquet 等'),
              ('读取与配置', '格式读取器 + 机器人配置；单位、通道、时钟与 Numeric 配置确认'),
              ('统一接口', 'Dataset → Episode → Channel；稳定 ID、源路径、selector'),
              ('能力检查与算法', '满足输入再执行；缺输入、缺标定、执行失败分别记录'),
              ('结果与证据', 'catalog → _runs；逐字段结果、事件、位置与原始证据')]
    return ('<section class="report-section" id="architecture" data-section="architecture"><h2>框架流程</h2><div class="architecture-flow">'
        + ''.join(f'<article><b>{i+1}. {esc(title)}</b><p>{esc(note)}</p></article>' for i, (title, note) in enumerate(stages))
        + '</div><p class="muted">处理状态与数据判断独立。各算法保留自己的评分，不生成跨算法总分；未完成的评估不能标为整体合格。</p></section>')


def record_detail(root, record, profile):
    ident = record['algorithm']['id']
    items = reports(record)
    parts = []
    if record.get('input_scope'):
        parts.append('<p>本次输入范围：' + esc(json.dumps(record['input_scope'], ensure_ascii=False)) + '</p>')
        if not record.get('scope_complete', True):
            parts.append('<p class="notice">声明的机械臂输入不完整；已有结果不代表所选范围全部达标。</p>')
    if record['status'] != 'completed':
        parts.append(f'<p class="notice">{esc(reason_text(record))}</p>')
    elif items:
        rows = []
        for item in items:
            channel, field = item.get('input_channel'), item.get('input_field')
            target = esc(item.get('input_label') or channel or item.get('name') or '本条记录')
            if item.get('input_label') and channel:
                target += f'<span class="muted block">关联来源通道：{esc(channel)}</span>'
            if field:
                target += f'<span class="muted block">字段：{esc(field)}</span>'
            mapping = profile.get('channels', {}).get(channel, {}).get('select') if channel else None
            if mapping and 'fk' in mapping:
                target += f'<span class="muted block">FK 派生位置，非新增样本：运动链 {esc(mapping["fk"])}</span>'
            elif mapping:
                target += (f'<span class="muted block">拆分视图，非新增样本：{esc(mapping.get("channel"))}'
                           f' · 索引 {esc(mapping.get("indices"))}</span>')
            verdict = VERDICTS.get(item.get('verdict'), item.get('verdict') or '未给出判定')
            notes = findings(item)
            note = '；'.join(notes) if notes else ('未发现该算法定义的异常' if item.get('verdict') == 'ACCEPTABLE' else '查看原始结果')
            rows.append(f'<tr><td>{target}</td><td>{esc(item.get("unit") or item.get("timestamp_unit") or "—")}</td>'
                        f'<td class="number">{score(item.get("score"))}</td><td>{esc(verdict)}</td><td>{esc(note)}</td></tr>')
        parts.append('<div class="scroll"><table><thead><tr><th>检查对象／字段</th><th>单位</th>'
                     '<th>原始评分</th><th>数据判定</th><th>主要发现</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
        if any(x.get('input_channel') for x in items):
            parts.append('<p class="muted">原通道与拆分视图可能对应同一份数据；这里不相加、不求平均，也不计为独立样本。</p>')
        for index, item in enumerate(items):
            cadence = item.get('cadence') or {}
            if cadence.get('channels'):
                cadence_rows = []
                for row in cadence['channels']:
                    def milliseconds(key):
                        value = row.get(key)
                        return '—' if value is None else f'{value * 1000:.6f}'
                    values = (row['channel'], row.get('time_source'), row.get('clock') or '未确认',
                              row['count'], milliseconds('median_positive_interval_s'),
                              milliseconds('max_interval_s'), row.get('zero_interval_count', '—'),
                              row.get('backward_interval_count', '—'),
                              '已观察' if row['status'] == 'observed' else row.get('reason', '缺输入'))
                    cadence_rows.append('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in values) + '</tr>')
                parts.append('<details class="subdetail"><summary>各路原始时间间隔（不代表跨路同步）</summary>'
                             '<div class="scroll"><table><tr><th>通道</th><th>时间来源</th><th>时钟</th>'
                             '<th>记录数</th><th>正间隔中位数 ms</th><th>最大间隔 ms</th><th>零间隔</th>'
                             '<th>倒退间隔</th><th>状态</th></tr>' + ''.join(cadence_rows) + '</table></div></details>')
            sub = item.get('sub_indicators')
            sub = sub if isinstance(sub, list) else []
            if not sub:
                continue
            title = item.get('input_label') or item.get('input_channel') or item.get('name') or f'输出 {index+1}'
            rows = []
            for value in sub:
                if not isinstance(value, dict):continue
                state = '未检查' if value.get('passed') is None else '通过' if value.get('passed') else '未通过'
                rows.append('<tr>' + ''.join(f'<td>{esc(x)}</td>' for x in (
                    value.get('name', ''), state, value.get('score') if value.get('score') is not None else '—',
                    value.get('raw_value', ''))) + '</tr>')
            parts.append(f'<details class="subdetail"><summary>{esc(title)}：分项指标</summary>'
                         '<div class="scroll"><table><tr><th>指标</th><th>状态</th><th>评分</th><th>依据</th></tr>'
                         + ''.join(rows) + '</table></div></details>')
    else:
        parts.append('<p>该记录没有结构化分项，请查看原始结果。</p>')
    links = []
    for index, ref in enumerate(record.get('artifacts', [])):
        safe_path(root, ref)
        links.append(f'<a href="{esc(quote(ref, safe="/"))}">证据 {index+1} · {esc(Path(ref).name)}</a>')
    if links:
        parts.append('<nav class="evidence">' + ' '.join(links) + '</nav>')
    parts.append('<details class="raw"><summary>开发者信息：原始记录 JSON</summary><pre>'
                 + esc(json.dumps(record, ensure_ascii=False, indent=2)) + '</pre></details>')
    numeric_scores = [x.get('score') for x in items]
    score_label = ''
    if record['status'] == 'completed' and numeric_scores and all(type(x) in (int, float) for x in numeric_scores):
        if len(numeric_scores) == 1:
            score_label = f'<span class="score-label">原始评分 {score(numeric_scores[0])}</span>'
        elif len(set(numeric_scores)) == 1:
            score_label = f'<span class="score-label">{len(numeric_scores)} 项输出各 {score(numeric_scores[0])} 分</span>'
        else:
            score_label = f'<span class="score-label">{len(numeric_scores)} 项独立评分，展开查看</span>'
    return (f'<details class="algorithm"><summary><span><strong>{esc(NAMES.get(ident, ident))}</strong>'
            f'<span class="muted algorithm-id">{esc(ident)} · {esc(record.get("instance_id", "episode"))}</span></span>{badge(record["status"])}{score_label}'
            f'<span class="brief">{brief(record)}</span></summary><div class="detail-body">'
            + ''.join(parts) + '</div></details>')


def render_run(root, entry, index, latest):
    snapshot = read_json(root, entry.get('run'))
    selection = read_json(root, entry.get('selection'))
    selections = {str(x['episode_id']): x for x in selection} if isinstance(selection, list) else {}
    groups = OrderedDict()
    for ref in entry['records']:
        for line in safe_path(root, ref).read_text(encoding='utf-8').splitlines():
            item = json.loads(line)
            key = (item['object']['type'], str(item['object']['id']))
            groups.setdefault(key, []).append(item)
    counts = Counter(item['status'] for records in groups.values() for item in records)
    total, done = sum(counts.values()), counts['completed']
    incomplete = total - done
    from edc.execution.completeness import record_complete
    partial = sum(not record_complete(r)
                  for records in groups.values() for r in records if r['status'] == 'completed')
    dataset = snapshot.get('dataset') or {}
    profile = snapshot.get('profile') or {}
    notes = ('本次评估尚未完成。未执行的算法没有质量结论，不能将已完成项目的分数当作整体合格。'
             if incomplete or partial else '所选算法均已执行。执行完成不等于数据合格，请查看各项数据判定。'
             if total else '本次运行尚无算法记录。')
    parts = [f'<div class="run-heading"><div><p class="eyebrow">{"最近一次运行" if latest else "历史运行"}</p>'
             f'<h2>{esc(dataset.get("id") or "数据质量评估")}</h2></div>'
             f'<span class="muted">运行 {esc(entry.get("run_id", index))}</span></div>',
             '<div class="stats">' + ''.join(f'<div><span>{label}</span><strong>{number}</strong></div>' for label, number in (
                 ('本次评估对象', len(groups)), ('已完成的算法记录', done), ('未完成的算法记录', incomplete),
                 ('执行失败', counts['processing_error']))) + '</div>',
             f'<p class="notice">{esc(notes)}</p>',
             '<p class="muted">覆盖范围仅限本次列出的对象与算法，不代表源目录或整个数据集已完成评估。各算法独立报告，不生成总分。</p>']
    pool_ref = entry.get('recommendations') or (snapshot.get('recommendations') or {}).get('path')
    pool = read_json(root, pool_ref) if pool_ref else None
    parts.append(configuration_panel(profile, snapshot.get('params') or {}, snapshot.get('unit_normalization'), pool))
    parts.append(numeric_panel([r for records in groups.values() for r in records], root))
    if dataset.get('source'):
        parts.append(f'<details class="source"><summary>查看数据来源与配置</summary><p>{esc(dataset["source"])}</p>'
                     f'<p>机器人配置：{esc(profile.get("id", "未提供"))} · 版本 {esc(profile.get("version", "未知"))}</p></details>')
    for (object_type, object_id), records in groups.items():
        note = ('数据集级结果单独报告，不据此生成逐轨迹筛选结论。' if object_type == 'dataset'
                else selection_note(selections.get(object_id)))
        parts.append(f'<section class="episode" data-section="algorithms"><h3>评估对象 <span>{esc(object_id)}</span></h3>'
                     f'<p class="assessment">{esc(note)}</p>')
        completed = [r for r in records if r['status'] == 'completed']
        pending = [r for r in records if r['status'] != 'completed']
        if completed:
            parts.append('<h4>已有结果 <span>点击算法查看逐字段评分与证据</span></h4>')
            parts.extend(record_detail(root, r, profile) for r in completed)
        if pending:
            parts.append('<h4>未完成的检查 <span>以下项目没有质量评分</span></h4><div class="scroll"><table>'
                         '<tr><th>算法</th><th>处理状态</th><th>缺少什么／失败原因</th></tr>')
            for r in pending:
                ident = r['algorithm']['id']
                parts.append(f'<tr><td>{esc(NAMES.get(ident, ident))}<span class="muted block">{esc(ident)}</span></td>'
                             f'<td>{badge(r["status"])}</td><td>{esc(reason_text(r))}</td></tr>')
            parts.append('</table></div><details class="subdetail"><summary>查看未完成项目的原始记录</summary>')
            parts.extend(record_detail(root, r, profile) for r in pending)
            parts.append('</details>')
        parts.append('</section>')
    for ref, title in ((entry.get('selection'), '筛选清单'), (entry.get('run'), '本次配置与版本'),
                       (entry.get('media_manifest'), '本次媒体复用清单')):
        if ref:
            safe_path(root, ref)
            parts.append(f'<a class="footer-link" href="{esc(quote(ref, safe="/"))}">{title}</a>')
    body = ''.join(parts)
    if latest:
        return '<section class="run" data-section="overview">' + body + '</section>'
    return f'<details class="history"><summary>历史运行 · {esc(entry.get("run_id", index))}</summary>{body}</details>'


STYLE = '''
:root{color-scheme:light;--ink:#192d3d;--muted:#586c7c;--line:#dce5eb;--brand:#216a72}
*{box-sizing:border-box}body{margin:0;background:#f3f6f8;color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:36px auto;padding:0 24px 48px;overflow-wrap:anywhere}header{margin-bottom:28px}h1{font-size:30px;margin:0 0 6px}
h2{font-size:23px;margin:0}h3{font-size:18px;margin:0 0 8px}h3 span{font-weight:500;overflow-wrap:anywhere}
h4{margin:24px 0 10px;font-size:16px}h4 span{font-weight:400;font-size:13px;color:var(--muted);margin-left:12px}
p{margin:10px 0}.muted{color:var(--muted);font-size:13px}.block{display:block}.eyebrow{text-transform:uppercase;color:var(--brand);font-weight:700;font-size:12px;margin:0 0 6px}
.run{background:#fff;border:1px solid var(--line);border-radius:16px;padding:28px}.run-heading{display:flex;gap:20px;justify-content:space-between;align-items:center;flex-wrap:wrap}
.run-heading>div{min-width:0;max-width:100%}.run-heading h2{overflow-wrap:anywhere}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0}.stats>div{padding:18px;background:#f4f8fa;border-radius:10px}.stats span{display:block;font-size:13px;color:var(--muted)}.stats strong{display:block;font-size:30px;font-weight:650;margin-top:4px}
.notice{padding:14px 18px;border-left:4px solid #ba791c;background:#fff7e9;color:#684810;border-radius:4px}
.episode{border-top:1px solid var(--line);margin-top:24px;padding-top:24px}.assessment{color:var(--muted)}
.algorithm{border:1px solid var(--line);border-radius:10px;margin:10px 0;overflow:hidden}.algorithm>summary{display:flex;align-items:center;flex-wrap:wrap;gap:10px 18px;padding:16px 18px;background:#fafcfd;cursor:pointer;list-style:none}.algorithm>summary:before{content:'▸';color:var(--brand)}.algorithm[open]>summary:before{content:'▾'}.algorithm-id{margin-left:10px}.brief{flex-basis:100%;padding-left:24px;font-size:14px}
.score-label{font-size:13px;font-weight:650;color:var(--ink)}
.badge{display:inline-block;white-space:nowrap;font-size:12px;font-weight:600;padding:3px 9px;border-radius:5px}.done{background:#e6f3f2;color:#185e61}.pending{background:#fff0d9;color:#805210}.error{background:#fde8e7;color:#973737}.detail-body{padding:16px 18px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;text-align:left}td,th{padding:12px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}th{background:#f3f7f9;font-weight:600}td:first-child{max-width:380px}.number{font-variant-numeric:tabular-nums;font-weight:650}
summary{cursor:pointer}summary:focus-visible,a:focus-visible{outline:3px solid #5098a8;outline-offset:3px}.subdetail,.raw,.source{margin-top:12px;font-size:13px}.subdetail>summary,.raw>summary,.source>summary{padding:7px 0;color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:14px;max-height:480px;overflow:auto;font-size:12px}
a{color:#155f83;text-underline-offset:3px}.evidence{display:flex;gap:16px;flex-wrap:wrap;margin-top:16px}.footer-link{display:inline-block;margin:24px 20px 0 0;font-size:13px}.history{margin-top:18px;padding:22px;background:white;border:1px solid var(--line);border-radius:12px}.history>summary{font-weight:600;margin-bottom:12px}
@media(max-width:700px){main{margin:20px auto;padding:0 12px 24px}.run{padding:18px}.stats{grid-template-columns:repeat(2,1fr)}.algorithm-id{display:block;margin-left:0}.detail-body{padding:12px}.run-heading{display:block}.run-heading>.muted{display:block;margin-top:8px;overflow-wrap:anywhere}}
@media print{body{background:white}main{max-width:none;margin:0}.run{border:0;padding:0}.stats{break-inside:avoid}.algorithm{break-inside:avoid}.raw,.history{display:none}}
.report-nav,.segments{display:flex;gap:12px;flex-wrap:wrap}.report-section{margin-top:28px;padding-top:20px;border-top:1px solid var(--line)}
.architecture-flow{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px}.architecture-flow article{border-top:3px solid var(--brand);padding:14px 0}.architecture-flow p{font-size:13px;color:var(--muted)}
.media-channel{margin:20px 0}.media-channel>summary{padding:10px 0;font-weight:600}.media-viewer{display:grid;grid-template-columns:1.2fr 1fr;gap:20px}.media-viewer>div{min-width:0}.media-viewer p{overflow-wrap:anywhere}video{width:100%;background:#15222a}.evidence-photos{display:grid;grid-template-columns:1fr 1fr;gap:12px}.evidence-photos figure{margin:0}.evidence-photos img{width:100%}figcaption{font-size:13px;color:var(--muted)}button{font:inherit;padding:9px 12px;border:1px solid var(--line);border-radius:5px;background:white;color:var(--ink);cursor:pointer}
@media(max-width:700px){.architecture-flow,.media-viewer{grid-template-columns:1fr}.numeric-fields{min-width:800px}}
'''


def render(output, catalog):
    entries = catalog.get('runs', [])
    body = ''.join(render_run(output, entry, index, index == len(entries)-1)
                   for index, entry in reversed(list(enumerate(entries))))
    if not body:
        body = '<p class="notice">暂无评估记录。请先运行所需算法。</p>'
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="edc-report-template" content="dqs_report_v2">'
            '<title>数据质量评估报告</title><style>' + STYLE + '</style></head><body><main>'
            '<header><p class="eyebrow">数据集 · 算法评估</p><h1>数据质量报告</h1>'
            '<p class="muted">先看完成情况与主要发现，再展开算法查看细节。原始结果和证据完整保留。</p>'
            '<nav class="report-nav"><a href="#runs">检查与参数确认</a><a href="#media">视频证据</a><a href="#architecture">框架流程</a></nav></header>'
            + '<div id="runs">' + body + '</div>' + media_panel(output, catalog.get('presentation'))
            + architecture_panel() + '</main><script data-edc-report="media">'
            'document.addEventListener("click",event=>{const button=event.target.closest("button[data-video]");if(!button)return;'
            'const video=document.getElementById(button.dataset.video),state=button.closest(".media-viewer").querySelector(".seek-state");'
            'const apply=()=>{video.currentTime=Number(button.dataset.seconds);};'
            'video.addEventListener("seeked",()=>{state.textContent="已定位到 "+video.currentTime.toFixed(3)+" 秒，点击播放器播放。";},{once:true});'
            'if(video.readyState>=1)apply();else video.addEventListener("loadedmetadata",apply,{once:true});});'
            '</script></body></html>')
