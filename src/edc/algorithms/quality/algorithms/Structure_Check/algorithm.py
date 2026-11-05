"""结构完整性：框架怎么调用这项检查（输入准备在这里，检查方法在 method.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm, too_short


def yaml_camera_pairs(profile, topics):
    """结构检查的“相机配对”（机器人 yaml）：每路相机按 yaml 的 info 找内参，没写或数据里没有都算没配上（扣分）。"""
    infos = profile.get('camera_info', {})
    pairs = [{'image': n, 'camera_info': infos.get(n), 'paired': bool(getattr(topics.get(infos.get(n)), 'count', 0))}
             for n, c in profile['channels'].items() if c.get('modality') in ('rgb', 'depth')]
    orphans = [p['image'] for p in pairs if not p['paired']]
    return {'image_count': len(pairs), 'paired_count': len(pairs) - len(orphans), 'orphan_images': orphans,
            'pairs': pairs, 'severity': 'warning' if orphans else 'pass'}


class Algorithm(BuiltinAlgorithm):
    name = '结构完整性'
    version = 'ir-adapter-16'
    descriptions = ('源文件与通道清单；相机配置一致性子项需要 camera_requirements 和 sensor_id/modality 映射。',)
    checks_short = True  # 太短的数据由本项排除，其它项不再检查

    def assess(self, ep, params, context):
        module, short = self.method(), too_short(ep)
        topics = {n: ep.raw(n) for n in ep.channels}
        check = module.check_structure
        if ep.profile.get('declared_only'):  # 机器人 yaml：相机内参按 yaml 写的 info 配对，不按名字猜
            module.check_structure = lambda *a, **k: {**check(*a, **k),
                                                      'camera_pairs': yaml_camera_pairs(ep.profile, topics)}
        try:
            result = module.assess(str(ep.path), topics, required_topics=ep.profile.get("required_channels"),
                camera_requirements=ep.profile.get("camera_requirements"),
                channel_config=ep.profile.get("channels", {}), **params)
        finally:
            module.check_structure = check
        pairs = (result.get('structure') or {}).get('camera_pairs') or {}
        bad = [p for p in pairs.get('pairs') or [] if not p['paired']]
        if bad:
            unwritten = sum(not p['camera_info'] for p in bad)
            result['unchecked'] = '相机配对：' + '，'.join(filter(None, [
                f'{unwritten} 路相机没写 info（内参）' if unwritten else '',
                f'{len(bad) - unwritten} 路写了 info 但数据里没有' if len(bad) > unwritten else ''])) + '，扣分'
        if short:
            result.update(original_verdict=result.get('verdict'), verdict='DO_NOT_USE', passed=False, too_short=short)
            result['reasons'] = [f"数据太短：只有 {short['seconds']:.1f} 秒，本机型要求至少 {short['min_duration_s']:g} 秒"]                     + list(result.get('reasons') or [])
        return result
