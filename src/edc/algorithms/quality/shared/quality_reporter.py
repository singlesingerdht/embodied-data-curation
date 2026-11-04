"""
画质质量过滤器 V3 + 传感器故障检测器
基于缺陷注入校准实验的数据驱动阈值, 分辨率自适应 (min(w,h) < 256)
连续分数体系: quality = (val - t_min) / (t_max - t_min), clamp [0,1]
"""
import numpy as np
from typing import List, Dict, Any, Optional, Tuple


# ═══════════════════════════════════════════
# 连续分数核心函数
# ═══════════════════════════════════════════

def to_grade(score: float) -> str:
    """连续分数 → 等级带"""
    if score >= 95: return 'S'
    if score >= 85: return 'A'
    if score >= 70: return 'B'
    if score >= 50: return 'C'
    return 'D'


def compute_quality(val: float, t_min: Optional[float], t_max: Optional[float],
                    direction: str = "forward") -> float:
    """
    计算原子层质量分 [0, 1]。
    - forward: 分数越高越好 (val >= t_max → 1.0, val <= t_min → 0.0)
    - inverse: 分数越低越好 (val <= t_max → 1.0, val >= t_min → 0.0)
    - binary:  val >= t_min → 1.0, else 0.0
    - t_min/t_max 为 None 时跳过 (返回 1.0)
    """
    if direction == "binary":
        if t_min is None:
            return 1.0
        return 1.0 if val >= t_min else 0.0
    if t_max is None or t_min is None:
        return 1.0  # 不可靠指标跳过
    if direction == "inverse":
        if val <= t_max:
            return 1.0
        if val >= t_min:
            return 0.0
        return max(0.0, min(1.0, (t_min - val) / (t_min - t_max)))
    # forward (default)
    if val <= t_min:
        return 0.0
    if val >= t_max:
        return 1.0
    return max(0.0, min(1.0, (val - t_min) / (t_max - t_min)))


# ═══════════════════════════════════════════
# T_max 查表 (从 Step 2 北京数据集均值计算得出)
# ═══════════════════════════════════════════

T_MAX_TABLE = {
    '640_head': {
        'Noise': 72.1, 'Low Fluency': 65.6, 'Motion Blur': 28.6,
        'Lighting': 57.8, 'Technical Perspective': 54.7,
        'Out of Focus': 66.9, 'Low Sharpness': 73.0,
        'Composition': 29.6, 'Contents': 57.2, 'Color': 54.4,
        'Compression Artifacts': 69.3, 'Poor Exposure': 74.1,
        'Good Aesthetics': 57.7, 'Flicker': 94.0,
        'Overall': 57.6, 'Trajectory': 74.3,
    },
    '640_wrist': {
        'Noise': 62.2, 'Low Fluency': 51.0, 'Motion Blur': 21.5,
        'Lighting': 58.9, 'Technical Perspective': 44.5,
        'Out of Focus': 59.5, 'Low Sharpness': 64.2,
        'Composition': 18.5, 'Contents': 46.1, 'Color': 58.3,
        'Compression Artifacts': 57.7, 'Poor Exposure': 68.8,
        'Good Aesthetics': 48.8, 'Flicker': 91.3,
        'Overall': 48.9, 'Trajectory': 64.4,
    },
    '224_head': {
        'Noise': 46.2, 'Low Fluency': 43.1, 'Motion Blur': 16.8,
        'Lighting': 39.3, 'Technical Perspective': 29.1,
        'Out of Focus': 44.2, 'Low Sharpness': 47.1,
        'Composition': 21.1, 'Contents': 34.8, 'Color': 30.6,
        'Compression Artifacts': 44.3, 'Poor Exposure': 59.6,
        'Good Aesthetics': 36.5, 'Flicker': 93.2,
        'Overall': 34.0, 'Trajectory': 66.7,
    },
    '224_wrist': {
        'Noise': 31.3, 'Low Fluency': 28.1, 'Motion Blur': 12.0,
        'Lighting': 42.3, 'Technical Perspective': 21.4,
        'Out of Focus': 36.0, 'Low Sharpness': 37.1,
        'Composition': 14.3, 'Contents': 24.8, 'Color': 36.0,
        'Compression Artifacts': 29.8, 'Poor Exposure': 56.3,
        'Good Aesthetics': 29.1, 'Flicker': 90.2,
        'Overall': 26.6, 'Trajectory': 57.2,
    },
}

DIRECTION = {
    'Noise': 'forward', 'Low Fluency': 'forward', 'Motion Blur': 'forward',
    'Lighting': 'forward', 'Technical Perspective': 'forward',
    'Out of Focus': 'forward', 'Low Sharpness': 'forward',
    'Composition': 'forward', 'Contents': 'forward', 'Color': 'forward',
    'Compression Artifacts': 'forward', 'Poor Exposure': 'forward',
    'Good Aesthetics': 'forward', 'Flicker': 'forward',
    'Overall': 'forward', 'Trajectory': 'forward',
}


def normalize_weights(defaults: Dict[str, float],
                      overrides: Optional[Dict[str, float]] = None,
                      module_name: str = "") -> Tuple[Dict[str, float], List[str]]:
    """
    权重归一化: 用户覆盖部分权重, 剩余按默认比例分配, 保证 Σ=1.0。
    Returns: (final_weights, warnings)
    """
    if not overrides:
        return dict(defaults), []

    # 只取与当前模块相关的覆盖
    relevant_overrides = {k: v for k, v in overrides.items() if k in defaults}
    if not relevant_overrides:
        return dict(defaults), []

    result = {}
    specified = {}
    unspecified = []

    for name, dw in defaults.items():
        if name in relevant_overrides and relevant_overrides[name] != 0:
            specified[name] = relevant_overrides[name]
            result[name] = relevant_overrides[name]
        else:
            unspecified.append(name)

    specified_sum = sum(specified.values())
    warnings = []

    if unspecified:
        remaining = 1.0 - specified_sum
        if remaining < 0:
            total = specified_sum
            acc = 0.0
            for i, name in enumerate(specified):
                if i == len(specified) - 1:
                    result[name] = round(1.0 - acc, 4)
                else:
                    w = round(specified[name] / total, 4)
                    result[name] = w
                    acc += w
            for name in unspecified:
                result[name] = 0
            warnings.append(f"{module_name}模块: 权重总和={specified_sum:.2f}>1.00, 已自动归一化")
        elif remaining > 0:
            share = remaining / len(unspecified)
            for i, name in enumerate(unspecified):
                if i == len(unspecified) - 1:
                    result[name] = round(1.0 - sum(result.values()), 4)  # 最后一项收尾
                else:
                    result[name] = round(share, 4)
            if abs(specified_sum - 1.0) > 0.0001 and specified_sum != 0:
                warnings.append(f"{module_name}模块: 用户指定权重总和={specified_sum:.2f}≠1.00, 剩余已自动分配")
        else:
            for name in unspecified:
                result[name] = 0
    else:
        if abs(specified_sum - 1.0) > 0.0001:
            acc = 0.0
            for i, name in enumerate(specified):
                if i == len(specified) - 1:
                    result[name] = round(1.0 - acc, 4)
                else:
                    w = round(specified[name] / specified_sum, 4)
                    result[name] = w
                    acc += w
            warnings.append(f"{module_name}模块: 权重总和={specified_sum:.2f}≠1.00, 已自动归一化")

    return result, warnings


# ═══════════════════════════════════════════
# 传感器故障检测器
# ═══════════════════════════════════════════

class SensorFaultDetector:
    """多指标联合判断: 每种故障需 >= 半数指标低于阈值才触发。224 下仅 noise/underexposure 可靠"""

    FAULT_CONFIGS = {
        'noise_sensor': (2, [
            ('Noise',                 44,  10),
            ('Low Fluency',           24,   3),
            ('Contents',              21,  None),
            ('Compression Artifacts',  None, 12),
            ('Low Sharpness',         None, 24),
        ]),
        'blur': (2, [
            ('Out of Focus',          42,  None),
            ('Low Sharpness',         44,  None),
            ('Contents',              23,  None),
        ]),
        'underexposure': (2, [
            ('Lighting',              43,  31),
            ('Color',                 41,  None),
        ]),
        'overexposure': (2, [
            ('Contents',              26,  None),
            ('Good Aesthetics',       33,  None),
        ]),
    }

    @classmethod
    def detect(cls, metrics: dict, is_low: bool = False) -> list:
        faults = []
        for sensor, (need, thresholds_list) in cls.FAULT_CONFIGS.items():
            triggered = 0; total = 0; details = []
            for metric, th_high, th_low in thresholds_list:
                th = th_low if is_low else th_high
                if th is None: continue
                total += 1
                score = metrics.get(metric, 100)
                if score < th:
                    triggered += 1
                    details.append(f'{metric}={score:.0f}<{th}')
            if total == 0: continue
            if triggered >= min(need, total):
                faults.append({
                    'sensor': sensor, 'triggered': f'{triggered}/{total}',
                    'details': details, 'ratio': triggered / total,
                })
        faults.sort(key=lambda x: -x['ratio'])
        return faults[:2]

    @classmethod
    def is_faulty(cls, metrics: dict, is_low: bool = False) -> bool:
        return len(cls.detect(metrics, is_low)) > 0


# ═══════════════════════════════════════════
# 画质质量过滤器 V3
# ═══════════════════════════════════════════

class EmbodiedQualityFilterV3:
    """
    分辨率感知质量筛选器 V3
    阈值 = min(北京校准, 成都全量) − 5, 保证跨数据集100%通过
    Normal (h >= 256): NORMAL_THRESHOLDS
    Low    (h <  256): LOW_THRESHOLDS
    """

    RESOLUTION_CRITICAL_BOUNDARY = 256

    NORMAL_THRESHOLDS = {
        'diagnostic': {
            'Noise': 41, 'Technical Perspective': 25, 'Low Fluency': 21,
            'Motion Blur': 11, 'Lighting': 44, 'Poor Exposure': 57,
            'Flicker': 80, 'Color': 38, 'Compression Artifacts': 40,
            'Out of Focus': 45, 'Composition': 9, 'Contents': 17,
            'Good Aesthetics': 10,
        },
        'head_strict': {
            'Low Fluency': 47, 'Motion Blur': 20, 'Low Sharpness': 45,
            'Trajectory': 45,
        },
    }

    LOW_THRESHOLDS = {
        'diagnostic': {
            'Noise': 16, 'Technical Perspective': 9, 'Low Fluency': 7,
            'Motion Blur': 2, 'Lighting': 28, 'Poor Exposure': 40,
            'Flicker': 80, 'Color': 18, 'Compression Artifacts': 12,
            'Out of Focus': 20, 'Composition': 6, 'Contents': 7,
            'Good Aesthetics': 10,
        },
        'head_strict': {
            'Low Fluency': 30, 'Motion Blur': 8, 'Low Sharpness': 30,
            'Trajectory': 40,
        },
    }

    CRITICAL_INDICATORS = ['Technical Perspective', 'Noise', 'Low Fluency',
                            'Motion Blur', 'Lighting']
    WARNING_INDICATORS = ['Out of Focus', 'Low Sharpness', 'Composition',
                            'Contents', 'Color', 'Compression Artifacts',
                            'Poor Exposure']

    def __init__(self, is_low: bool = False):
        self.is_low = is_low

    @staticmethod
    def get_tier(resolution: tuple = None) -> str:
        if resolution is None: return "Normal"
        if isinstance(resolution, (list, tuple)):
            short = min(resolution[0], resolution[1])
        else:
            short = resolution
        return "Low" if short < EmbodiedQualityFilterV3.RESOLUTION_CRITICAL_BOUNDARY else "Normal"

    def _thresholds(self):
        src = self.LOW_THRESHOLDS if self.is_low else self.NORMAL_THRESHOLDS
        return src['diagnostic'].copy(), src['head_strict'].copy()

    def assess(self, metrics: dict, cam_type: str = 'wrist') -> dict:
        diag, head = self._thresholds()
        thresholds = diag.copy()
        if cam_type == 'head':
            thresholds.update(head)

        critical_issues = []
        warning_issues = []
        is_critical_fail = False

        for key, val in metrics.items():
            if key in thresholds:
                th = thresholds[key]
                if val < th:
                    msg = f"{key}: {val:.1f} (Low < {th})"
                    if key in self.CRITICAL_INDICATORS:
                        critical_issues.append(msg)
                        is_critical_fail = True
                    elif key in self.WARNING_INDICATORS:
                        warning_issues.append(msg)

        return {
            'is_critical_fail': is_critical_fail,
            'critical_issues': critical_issues,
            'warning_issues': warning_issues,
        }

    def assess_batch(self, batch_data: list) -> list:
        results = []
        for item in batch_data:
            m = item.get('metrics', {})
            cam = item.get('camera_type', 'wrist')
            r = self.assess(m, cam)
            results.append({
                'id': item.get('id', 'unknown'),
                'camera_type': cam,
                'final_decision': 'REJECT' if r['is_critical_fail'] else 'PASS',
                'critical_issues': r['critical_issues'],
                'warning_issues': r['warning_issues'],
                'all_metrics_summary': m,
                'resolution_tier': 'Low' if self.is_low else 'Normal',
            })
        return results

    # ── 统一权重评分 (连续分数 + 熔断 + 归因) ──

    def compute_weighted_scores(self, metrics: dict, cam_type: str,
                                 sensor_faults: list) -> dict:
        """
        B1/B2/B3 统一质量分计算 (连续分数体系)。
        - B1 致命画质: 5 原子, 任一 quality==0 → 熔断 B1=0
        - B2 传感器: 4 复合, 任一传感器内原子==0 → 传感器=0, 任一传感器=0 → B2=0
        - B3 画质警告: 7 原子, 不熔断, 仅出分
        score = 100 × quality (不含权重, 权重在 router 层应用)
        """
        is_head = (cam_type == 'head')
        res_key = f"{'224' if self.is_low else '640'}_{'head' if is_head else 'wrist'}"
        t_max = T_MAX_TABLE[res_key]

        # ── B1: 致命画质 (5 原子, 熔断) ──
        # (name, t_min_640wrist, t_min_640head, t_min_224wrist, t_min_224head)
        b1_atoms = [
            ('Noise', 41, 41, 16, 16),
            ('Low Fluency', 21, 47, 7, 30),
            ('Motion Blur', 11, 20, 2, 8),
            ('Lighting', 44, 44, 28, 28),
            ('Technical Perspective', 25, 25, 9, 9),
        ]
        b1_any_zero = False
        b1_scores = []
        b1_details = []
        for name, t640_w, t640_h, t224_w, t224_h in b1_atoms:
            val = metrics.get(name, 0)
            if self.is_low:
                t_min = t224_h if is_head else t224_w
            else:
                t_min = t640_h if is_head else t640_w
            t_max_val = t_max.get(name, 100)
            q = compute_quality(val, t_min, t_max_val, DIRECTION.get(name, 'forward'))
            s = round(q * 100, 1)
            b1_scores.append(s)
            b1_details.append({
                'name': name, 'val': round(val, 1), 't_min': t_min, 't_max': t_max_val,
                'quality': round(q, 3), 'score': s, 'gap': round(100 - s, 1),
            })
            if s == 0:
                b1_any_zero = True

        if b1_any_zero:
            b1_score = 0; b1_passed = False
        else:
            b1_mean = sum(b1_scores) / len(b1_scores)
            b1_score = round(b1_mean, 1); b1_passed = True

        b1_subs = [{
            'name': '致命画质', 'default_weight': 0.50,
            'direction': 'continuous',
            'threshold_rule': '任一 S_atomic==0 → B1=0, 否则 mean×0.50',
            'raw_value': '; '.join(f"{d['name']}={d['val']:.0f}/{d['t_min']}" for d in b1_details),
            'quality': round(b1_score / 100, 3) if b1_passed else 0,
            'passed': b1_passed, 'score': b1_score,
            'details': b1_details,
        }]

        # ── B2: 传感器 (4 复合, 一票否决) ──
        sensors = {
            'noise_sensor': (2, [
                ('Noise', 44, 10), ('Low Fluency', 24, 3), ('Contents', 21, None),
            ]),
            'blur': (2, [
                ('Out of Focus', 42, None), ('Low Sharpness', 44, None), ('Contents', 23, None),
            ]),
            'underexposure': (2, [
                ('Lighting', 43, 31), ('Color', 41, None),
            ]),
            'overexposure': (2, [
                ('Contents', 26, None), ('Good Aesthetics', 33, None),
            ]),
        }
        b2_any_zero = False
        b2_sensor_scores = []
        b2_details = []

        for s_name, (need, atoms) in sensors.items():
            any_zero = False
            s_scores = []
            s_details = []
            for m_name, t_min_h, t_min_l in atoms:
                t_min = t_min_l if self.is_low else t_min_h
                if t_min is None:
                    continue
                val = metrics.get(m_name, 100)
                t_max_val = t_max.get(m_name, 100)
                q = compute_quality(val, t_min, t_max_val, DIRECTION.get(m_name, 'forward'))
                s = round(q * 100, 1)
                s_scores.append(s)
                s_details.append({
                    'name': m_name, 'val': round(val, 1), 't_min': t_min, 't_max': t_max_val,
                    'quality': round(q, 3), 'score': s, 'gap': round(100 - s, 1),
                })
                if s == 0:
                    any_zero = True

            if not s_scores:
                continue
            sensor_score = 0 if any_zero else round(sum(s_scores) / len(s_scores), 1)
            b2_sensor_scores.append(sensor_score)
            b2_details.append({
                'name': s_name, 'score': sensor_score, 'fault': any_zero,
                'atoms': s_details,
            })
            if any_zero:
                b2_any_zero = True

        if b2_any_zero or not b2_sensor_scores:
            b2_score = 0; b2_passed = False
        else:
            b2_mean = sum(b2_sensor_scores) / len(b2_sensor_scores)
            b2_score = round(b2_mean, 1); b2_passed = True

        b2_subs = [{
            'name': '传感器信号', 'default_weight': 0.30,
            'direction': 'composite',
            'threshold_rule': '任一传感器=0 → B2=0, 否则 mean×0.30',
            'raw_value': '; '.join(f"{d['name']}={d['score']:.0f}" for d in b2_details),
            'quality': round(b2_score / 100, 3) if b2_passed else 0,
            'passed': b2_passed, 'score': b2_score,
            'details': b2_details,
        }]

        # ── B3: 画质警告 (7 原子, 不熔断) ──
        b3_metrics = [
            ('Out of Focus', 45, 20), ('Low Sharpness', 45, 30),
            ('Composition', 9, 6), ('Contents', 17, 7),
            ('Color', 38, 18), ('Compression Artifacts', 40, 12),
            ('Poor Exposure', 57, 40),
        ]
        b3_scores = []
        b3_details = []
        for name, t_min_n, t_min_l in b3_metrics:
            val = metrics.get(name, 0)
            t_min = t_min_l if self.is_low else t_min_n
            t_max_val = t_max.get(name, 100)
            q = compute_quality(val, t_min, t_max_val, DIRECTION.get(name, 'forward'))
            s = round(q * 100, 1)
            b3_scores.append(s)
            b3_details.append({
                'name': name, 'val': round(val, 1), 't_min': t_min, 't_max': t_max_val,
                'quality': round(q, 3), 'score': s, 'gap': round(100 - s, 1),
            })

        b3_mean = sum(b3_scores) / len(b3_scores)
        b3_score = round(b3_mean, 1); b3_passed = True

        b3_subs = [{
            'name': '画质警告', 'default_weight': 0.20,
            'direction': 'continuous',
            'threshold_rule': '不熔断, 仅出分 + 归因',
            'raw_value': '; '.join(f"{d['name']}={d['val']:.0f}/{d['t_min']}" for d in b3_details),
            'quality': round(b3_mean / 100, 3),
            'passed': b3_passed, 'score': b3_score,
            'details': b3_details,
        }]

        # ── 未启用指标 (w=0) ──
        unused = []
        for k in ['Overall', 'Trajectory', 'Good Aesthetics', 'Flicker']:
            v = metrics.get(k, None)
            if v is not None:
                unused.append({
                    'name': f'({k})', 'default_weight': 0, 'weight': 0,
                    'direction': 'binary',
                    'threshold_rule': '暂不启用',
                    'raw_value': f'{k}={v:.0f}',
                    'quality': 0, 'passed': True, 'score': 0,
                    't_min': None, 't_max': None, 'current_value': round(v, 1),
                    'gap': 0, 'weighted_gap': 0, 'details': [],
                })

        return {
            'b1': {'subs': b1_subs, 'passed': b1_passed, 'score': b1_score},
            'b2': {'subs': b2_subs, 'passed': b2_passed, 'score': b2_score},
            'b3': {'subs': b3_subs, 'passed': b3_passed, 'score': b3_score},
            'unused': unused,
            'b_passed': b1_passed and b2_passed,
        }
