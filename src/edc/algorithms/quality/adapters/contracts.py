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
    'Action_Smoothness': ('末端反馈 position 换算为 m；native 使用自身时间，reference_channel 使用指定通道真实 acquisition 时间并要求共同 clock 或显式映射，reference_rate 只借参考通道帧间隔、在自身时间上取原始行。频率仅作标定检查，不生成时间。',),
    'Reset_Detection': ('关节 position；已确认的复位 reference、单位与正 tolerance，且与实际输入量纲一致。',),
    'Video_Integrity': ('可解码 RGB 或原视频；默认auto按读取器声明选择图像或视频入口；frames无需编码；video物化另需显式 FPS 或有效时间轴。',
                        '采样间隔和空间缩小比例单独记录；派生视频结果不能证明原编码或采集同步。'),
}


def cpu_parameters(ident):
    properties = {'channels': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'},
        'description': '选择源 topic/Channel 名称或机器人配置的 logical_name；支持多个，缺失或解析后重复拒绝，不猜测或回退。不填按 arms 与声明的输入要求选择。'}}
    properties['arms'] = {'type': ['string', 'array'], 'items': {'type': 'string'},
                          'description': 'all 或已声明的机械臂部件 ID 列表；默认按机器人拓扑选择全部臂'}
    if ident != 'Reset_Detection':
        properties['weights'] = {'type': ['object', 'null'], 'description': '原方法的子项权重；未提供时沿用原默认值'}
    else:
        properties['split_segments'] = {'type': 'boolean', 'default': False,
            'description': '数据集自带剪辑记录（数据目录里的 anno/source_segment.json，或上级目录 split_manifest.json 列出本条）时，'
                           '这条是从长录制剪出来的切片：只查真正的录制开头（从原始第 0 帧剪起时查开头），其余端不查；两端都不查时记为不适用'}
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
        properties.update(
            timeline={'type': 'object', 'additionalProperties': False, 'default': {'mode': 'native'},
                'properties': {
                    'mode': {'type': 'string', 'enum': ['native', 'reference_channel', 'reference_rate'], 'default': 'native',
                             'description': 'reference_rate：只借参考通道自身时间轴上的帧间隔，在源自身时间轴上每隔 k 行取 1 行原始样本；不比较两个钟，不插值'},
                    'reference_channel': {'type': 'string', 'description': '参考通道的源 topic 或逻辑名；使用真实时间点'},
                    'time_source': {'type': 'string', 'enum': ['acquisition', 'container']},
                    'resample': {'type': 'string', 'enum': ['linear', 'keep_every_kth_row'], 'default': 'linear'},
                    'clock_policy': {'type': 'string', 'enum': ['require_common_or_explicit_mapping'],
                                     'default': 'require_common_or_explicit_mapping'}},
                'description': '算法输入时间轴；参考模式仅接受 acquisition，不外推。缺共同 clock 或映射时 NEEDS_REVIEW'},
            expected_rate_hz={'type': 'number', 'exclusiveMinimum': 0, 'default': 30,
                'description': '滤波与阈值的标定频率，只检查真实时间轴，不生成均匀时间'},
            rate_tolerance_ratio={'type': 'number', 'minimum': 0, 'maximum': 1, 'default': .2,
                'description': '实测中位频率相对标定频率的允许偏差，超出只保留诊断值并待复核'},
            window_length={'type': 'integer', 'minimum': 5, 'default': 11,
                'description': 'Savitzky-Golay 窗口帧数，必须为奇数；三阶多项式保持不变'},
            vel_threshold_factor={'type': 'number', 'exclusiveMinimum': 0, 'default': .1,
                'description': '静止分段速度门限与全局峰值之比'},
            jerk_threshold_m_s3={'type': 'number', 'exclusiveMinimum': 5, 'default': 15,
                'description': 'SI 残差 jerk 阈值；大于保留的 5 m/s³ 满分基线'},
            jerk_frames={'type': 'string', 'enum': ['method', 'slow_only'], 'default': 'method',
                'description': 'method 沿用原方法（静止段两侧补帧后算抖动）；slow_only 只用原方法自己找出的真正慢下来的帧'
                               '（原方法已输出的 raw_stationary_jerk_rms），同一指标、同一阈值，原分数保留为 method_score'})
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
    if ident in {'Check_Timestamp', 'Sync_Check', 'Root_Cause'}:
        properties['target_clock'] = {'type': 'string',
            'description': '执行时使用的共同clock；仅应用配置中明确的仿射映射，不重采样、不修改原时间或时间来源'}
    if ident == 'Check_Timestamp':
        properties['scoring'] = {'type': 'object', 'description': '本机型的评分参数（原方法命令行选项）：'
            'absolute_error_floor_ms、dt_cv_no_penalty、dt_cv_zero、long_gap_ratio_warning、long_gap_ratio_zero、iou_threshold'}
        properties['count_each_source_once'] = {'type': 'boolean', 'default': False,
            'description': '拆分或推算出来的通道（与源话题同一时间轴）不重复计分'}
        properties['shared_row_max_gap_s'] = {'type': 'number', 'exclusiveMinimum': 0,
            'description': '所有通道共用一条行时间（导出时已逐帧对齐）时，相邻两行最多相隔多少秒；超过算空档、进复核。不填则只做观察'}
    if ident == 'Root_Cause':
        properties['families'] = {'type': ['object', 'null'], 'description': '显式消息源分组，用于关联异常；未填沿用原方法分组'}
    if ident == 'Sync_Check':
        properties['primary'] = {'type': ['string', 'null'], 'description': '参考采集通道；不填沿用原方法的参考选择'}
    if ident == 'Video_Integrity':
        properties.update(
            input_mode={'type': 'string', 'enum': ['auto', 'video', 'frames'], 'default': 'auto',
                'description': 'auto按原生存储选择：内嵌图像走frames，原编码视频走video；video按需物化；frames不编码MP4，容器子项不适用且不能配置视频综合weights'},
            sample_step={'type': 'integer', 'minimum': 1, 'default': 5, 'description': '原入口抽帧间隔；设为 1 才是逐帧'},
            downscale={'type': 'integer', 'minimum': 1, 'default': 4, 'description': '宽高各自缩小倍数；设为 1 保持空间分辨率'},
            cam_type={'type': 'string', 'enum': ['head', 'wrist'], 'default': 'wrist',
                      'description': '保留兼容参数；公共入口的安装位置只取机器人配置，当前评分阈值为相机通用，不按topic猜测'})
    schema = {'type': 'object', 'properties': copy.deepcopy(properties), 'additionalProperties': False}
    return {k: v['description'] for k, v in properties.items()}, schema
