"""视频完整性：框架怎么调用这项检查（选图片或视频入口在这里，适配在 inputs.py，检查方法在 method.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm
from edc.algorithms.quality.algorithms.Video_Integrity.inputs import input_mode


def video_modes(episode, params):
    return {c.name: input_mode(episode, c, params) for c in episode.channels.values()
            if c.modality == 'rgb' and c.image_available}


class Algorithm(BuiltinAlgorithm):
    name = '视频完整性'
    version = 'ir-adapter-17'
    inputs = ('video',)
    dependencies = ('cv2', 'xxhash')
    instance_mode = 'rgb'
    descriptions = ('可解码 RGB 或原视频；默认auto按读取器声明选择图像或视频入口；frames无需编码；video物化另需显式 FPS 或有效时间轴。',
                    '采样间隔和空间缩小比例单独记录；派生视频结果不能证明原编码或采集同步。')
    parameters = {
        'freeze_motion_review': {'type': 'object', 'default': {}, 'additionalProperties': True,
            'description': '相机逻辑名 → position运动反馈通道列表；候选重复区间附SI运动曲线、图片与来源。共同clock或显式映射不足时说明不可用，不改评分或判断'},
        'input_mode': {'type': 'string', 'enum': ['auto', 'video', 'frames'], 'default': 'auto',
            'description': 'auto按原生存储选择：内嵌图像走frames，原编码视频走video；video按需物化；frames不编码MP4，容器子项不适用且不能配置视频综合weights'},
        'sample_step': {'type': 'integer', 'minimum': 1, 'default': 5, 'description': '原入口抽帧间隔；设为 1 才是逐帧'},
        'downscale': {'type': 'integer', 'minimum': 1, 'default': 4, 'description': '宽高各自缩小倍数；设为 1 保持空间分辨率'},
        'cam_type': {'type': 'string', 'enum': ['head', 'wrist'], 'default': 'wrist',
            'description': '保留兼容参数；公共入口的安装位置只取机器人配置，当前评分阈值为相机通用，不按topic猜测'}}

    def check_inputs(self, episode, params):
        if not any(c.modality == 'rgb' and c.image_available for c in episode.channels.values()):
            return 'missing_input', 'Select at least one readable RGB channel; depth is not RGB'
        try:
            video_modes(episode, params)
        except ValueError as exc:
            return 'missing_input', str(exc)

    def available(self, episode, params):
        inputs = [{'channel': c.name, 'input_mode': input_mode(episode, c, params)}
                  for c in episode.channels.values() if c.modality == 'rgb' and c.image_available]
        partial = any(row['input_mode'] == 'frames' for row in inputs)
        return {'availability': 'partial' if partial else 'available', 'inputs': inputs,
                'reason': '图像入口直接检查解码画面；视频容器子项不适用，不生成完整视频综合分。' if partial else '',
                'scope': 'native_image_or_video'}

    def preflight(self, episode, params):
        modes = video_modes(episode, params)
        if 'video' in modes.values() and episode.format in {'hdf5', 'parquet', 'episode', 'lerobot'}:
            fps = episode.profile.get('video_fps') or episode.selector.get('info', {}).get('fps')
            from pathlib import Path
            unmaterializable = [c.name for c in episode.channels.values() if modes.get(c.name) == 'video'
                                and Path(c.source).suffix.lower() not in {'.mp4', '.avi', '.mov', '.mkv'}
                                and (c.timestamps is None or len(c.timestamps) < 2) and not fps]
            if unmaterializable:
                return 'missing_input', 'Embedded video requires explicit FPS or a source timeline: ' + ', '.join(unmaterializable)
        return 'runnable', ''

    def executables(self, episode, params):
        return ('ffmpeg', 'ffprobe') if 'video' in video_modes(episode, params).values() else ()

    def assess(self, ep, params, context):
        from edc.algorithms.quality.algorithms.Video_Integrity.inputs import assess
        return assess(ep, params, context)
