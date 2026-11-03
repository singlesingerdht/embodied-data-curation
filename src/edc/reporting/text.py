"""Text helpers shared by the result translation and the batch report: readable algorithm names, HTML escaping,
an algorithm's reports and the findings it wrote. Judges nothing."""
import html

NAMES = {
    'Structure_Check': '结构完整性', 'Numeric_Check': '数值检查',
    'Action_Smoothness': '动作平滑度', 'Check_Timestamp': '时间戳对齐',
    'Sync_Check': '采集同步', 'Root_Cause': '时间异常归因',
    'Dedup_Check': '时间／序列重复检查', 'Reset_Detection': '复位检测',
    'Video_Integrity': '视频完整性',
}


def esc(value):
    return html.escape(str(value), quote=True)


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
