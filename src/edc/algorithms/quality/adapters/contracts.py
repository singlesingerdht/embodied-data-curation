"""Parameters accepted by existing CPU adapters, without changing method defaults."""
import copy

INPUT_DESCRIPTIONS = {
    'Structure_Check': ('源文件与通道清单；相机配置一致性子项需要 camera_requirements 和 sensor_id/modality 映射。',),
    'Numeric_Check': ('数值字段可做 NaN/Inf 检查；物理检查还需明确物理量、可转换到 SI 的单位、标定及作者确认。',),
    'Dedup_Check': ('至少一条非合成原始时间序列；单位未知仍可比较相等，不能据此评估采集间隔或同步。',
                    '有效序列号为可选输入；不检查图像或轨迹内容，不自动删除记录。'),
    'Check_Timestamp': ('已有、单位明确的记录时间可逐路观察间隔；公共行时间只表示公共行节奏。',
                        '跨通道对齐评分另需独立时间轴、共同 clock、有效时间以及相机参考。'),
    'Sync_Check': ('至少两条独立原始采集时间轴、有效秒制时间和共同 clock；公共行时间或合成时间不可替代。',),
    'Root_Cause': ('至少两条可比较的独立记录时间轴及共同 clock；公共行时间不能用于跨通道归因。',),
    'Action_Smoothness': ('所选机械臂各自的末端 position，可换算为 m，并有逐行有效记录时间；不默认补帧率。',),
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
    if ident == 'Numeric_Check':
        properties['thresholds'] = {'type': 'object', 'description': '覆盖 robot.yaml 的 numeric_check.thresholds；变更后作者确认失效'}
    if ident in {'Check_Timestamp', 'Sync_Check', 'Root_Cause'}:
        properties['target_clock'] = {'type': 'string',
            'description': '执行时使用的共同clock；仅应用配置中明确的仿射映射，不重采样、不修改原时间或时间来源'}
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
