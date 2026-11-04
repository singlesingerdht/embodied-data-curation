"""Parameters accepted by existing CPU adapters, without changing method defaults."""
import copy

INPUT_DESCRIPTIONS = {
    'Structure_Check': ('源文件与通道清单；相机配置一致性子项需要 camera_requirements 和 sensor_id/modality 映射。',),
    'Numeric_Check': ('数值字段可做 NaN/Inf 检查；物理检查还需明确物理量、可转换到 SI 的单位、标定及 Numeric 配置确认。',
                      '动作位置指令需要独立 command_limits.limits；数值冻结只报告同 Episode 逐分量恒值区间，运动后恒值需复核。'),
    'Dedup_Check': ('至少一条非合成原始时间序列；单位未知仍可比较相等，不能据此评估采集间隔或同步。',
                    '有效序列号为可选输入；不检查图像或轨迹内容，不自动删除记录。'),
    'Check_Timestamp': ('已有、单位明确的记录时间可逐路观察间隔；公共行时间只表示公共行节奏。',
                        '跨通道对齐评分另需独立时间轴、共同 clock、有效时间以及相机参考。'),
    'Sync_Check': ('至少两条独立原始采集时间轴、有效秒制时间和共同 clock；公共行时间或合成时间不可替代。',),
    'Root_Cause': ('至少两条可比较的独立记录时间轴及共同 clock；公共行时间不能用于跨通道归因。',),
    'Action_Smoothness': ('末端反馈 position 换算为 m，用该通道自身的时间（采集时间、容器时间或格式规定的帧时间）；采样率低于 min_rate_hz 时不适用。',),
    'Reset_Detection': ('关节 position；已确认的复位 reference、单位与正 tolerance，且与实际输入量纲一致。',),
    'Video_Integrity': ('可解码 RGB 或原视频；默认auto按读取器声明选择图像或视频入口；frames无需编码；video物化另需显式 FPS 或有效时间轴。',
                        '采样间隔和空间缩小比例单独记录；派生视频结果不能证明原编码或采集同步。'),
}


def cpu_parameters(ident):
    properties = {'channels': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'},
        'description': '选择源 topic/Channel 名称或机器人配置的 logical_name；支持多个，缺失或解析后重复拒绝，不猜测或回退。不填按 arms 与声明的输入要求选择。'}}
    properties['arms'] = {'type': ['string', 'array'], 'items': {'type': 'string'},
                          'description': 'all 或已声明的机械臂部件 ID 列表；默认按机器人拓扑选择全部臂'}
    properties['skip'] = {'type': 'boolean', 'description': '由 yaml 的 checks.skip 生成：这项不查'}
    if ident not in ('Reset_Detection', 'Action_Smoothness', 'Sync_Check'):
        properties['weights'] = {'type': ['object', 'null'], 'description': '原方法的子项权重；未提供时沿用原默认值'}
    if ident == 'Dedup_Check':
        properties['channel'] = {'type': ['string', 'null'], 'description': '指定原始时间通道；不填时检查各路，公共行时间只检查一次'}
        properties['dedup_ratio_low'] = {'type': 'number', 'minimum': 0, 'maximum': 1, 'default': .01,
            'description': '原有长重复段评分的警告比例阈值，严格大于才触发；不用于新短段或非相邻重复'}
        properties['dedup_ratio_high'] = {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1, 'default': .05,
            'description': '原有长重复段评分的错误比例阈值，达到即触发；必须大于警告阈值'}
    if ident == 'Action_Smoothness':
        properties['channel'] = {'type': 'string', 'description': '精确绑定实际末端 position topic；与 channels 二选一，不自动改为同侧另一通道'}
        properties['side'] = {'type': 'string', 'enum': ['left', 'right'],
                              'description': '显式绑定的侧别校验；统一配置使用 arms 选择机械臂并逐臂绑定，禁止另一侧回退'}
        from edc.algorithms.quality.algorithms.Action_Smoothness.shake import DEFAULTS
        number = lambda key, text: {'type': 'number', 'exclusiveMinimum': 0, 'default': DEFAULTS[key], 'description': text}
        properties.update(
            band_hz={'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'default': DEFAULTS['band_hz'],
                     'description': '抖动的运动频率范围（Hz，每秒来回几次）：低于下限算有意动作，上限还受 band_rate_ratio 限制'},
            band_rate_ratio=number('band_rate_ratio', '上限最多取采样率的这个倍数（30 Hz 采样 → 10 Hz，15 Hz 采样 → 6 Hz）'),
            min_rate_hz=number('min_rate_hz', '采样率低于这个值时不检查平滑度（记为不适用）'),
            still_speed_m_s=number('still_speed_m_s', '有意动作的速度低于这个值（米/秒）算停住'),
            still_min_s=number('still_min_s', '低速至少持续这么多秒才算停住'),
            still_review_m_s2=number('still_review_m_s2', '停住时抖动加速度（均方根，米/秒²）超过这个值进复核；运动时只列数值不判断'),
            freeze_min_s=number('freeze_min_s', '运动中位置读数完全不变至少这么多秒，算读数冻结（复核）'),
            freeze_speed_m_s=number('freeze_speed_m_s', '冻结前 0.1 秒的速度至少这么快（米/秒），才算“运动中”冻结'),
            gap_factor=number('gap_factor', '相邻两行相隔超过中位间隔的这个倍数，算时间断档，两边分开算'),
            edge_s=number('edge_s', '每段开头结尾各舍弃这么多秒（滤波在两端不准）'),
            min_piece_s=number('min_piece_s', '断档之间短于这么多秒的数据段不算'))
    if ident == 'Numeric_Check':
        properties['thresholds'] = {'type': 'object', 'description': '覆盖 robot.yaml 的 numeric_check.thresholds；变更后 Numeric 配置确认失效'}
        properties['freeze_min_duration_s'] = {'type': 'number', 'exclusiveMinimum': 0,
            'description': '动过之后数值连续不变短于这个秒数的，视为正常停住，不进复核；不填按原方法的样本数规则'}
        properties['joint_stuck'] = {'type': 'object',
            'description': '本机型关节停住时读数完全不变（如 X1 Pro）时使用：不再把单个关节“动过之后不变”列为冻结候选，'
                           '改为整条手臂所有关节同时不变至少 freeze_min_duration_s 秒、且同一手臂末端移动至少 min_move_m 米才算关节读数卡住；'
                           'verdict 为卡住时的结论（默认 DO_NOT_USE）'}
        properties['joint_limits_pending'] = {'type': 'string',
            'description': '本机型还没有关节限位来源（如没有 URDF）时写明原因：关节限位子项记为不适用并显示原因；已填 joint.limits 时不起作用'}
        properties['finiteness_only'] = {'type': 'array', 'items': {'type': 'string'},
            'description': '“通道.字段”列表：本机型只查 NaN/Inf、视为检查完成；只允许没有物理检查项的字段'}
        properties['ranges'] = {'type': 'object',
            'description': '{“通道.字段”: {range: [最小, 最大], unit}}：机器人 yaml 写了 range 的项，超出范围判排除（由 yaml 自动生成）'}
    if ident in {'Check_Timestamp', 'Sync_Check', 'Root_Cause'}:
        properties['record_header_deviation_threshold_ms'] = {'type': ['number', 'null'], 'minimum': 0, 'default': None,
            'description': '记录时间减header原值，列中位差与最大偏离；仅显式填写时列绝对偏离超限项（明细最多20条），不改变评分或判断'}
        properties['target_clock'] = {'type': 'string',
            'description': '执行时使用的共同clock；仅应用配置中明确的仿射映射，不重采样、不修改原时间或时间来源'}
    if ident == 'Check_Timestamp':
        properties['shared_row_max_gap_s'] = {'type': 'number', 'exclusiveMinimum': 0,
            'description': '所有通道共用一条行时间（导出时已逐帧对齐）时，相邻两行最多相隔多少秒；超过算空档、进复核。不填按中位行间隔的 3 倍'}
    if ident == 'Root_Cause':
        properties['families'] = {'type': ['object', 'null'], 'description': '原始话题的消息源分组（拆出的通道跟源话题走，不用写），用于关联异常；未填沿用原方法分组'}
    if ident == 'Video_Integrity':
        properties['freeze_motion_review'] = {'type': 'object', 'default': {},
            'additionalProperties': True,
            'description': '相机逻辑名 → position运动反馈通道列表；候选重复区间附SI运动曲线、图片与来源。共同clock或显式映射不足时说明不可用，不改评分或判断'}
        properties.update(
            input_mode={'type': 'string', 'enum': ['auto', 'video', 'frames'], 'default': 'auto',
                'description': 'auto按原生存储选择：内嵌图像走frames，原编码视频走video；video按需物化；frames不编码MP4，容器子项不适用且不能配置视频综合weights'},
            sample_step={'type': 'integer', 'minimum': 1, 'default': 5, 'description': '原入口抽帧间隔；设为 1 才是逐帧'},
            downscale={'type': 'integer', 'minimum': 1, 'default': 4, 'description': '宽高各自缩小倍数；设为 1 保持空间分辨率'},
            cam_type={'type': 'string', 'enum': ['head', 'wrist'], 'default': 'wrist',
                      'description': '保留兼容参数；公共入口的安装位置只取机器人配置，当前评分阈值为相机通用，不按topic猜测'})
    schema = {'type': 'object', 'properties': copy.deepcopy(properties), 'additionalProperties': False}
    return {k: v['description'] for k, v in properties.items()}, schema
