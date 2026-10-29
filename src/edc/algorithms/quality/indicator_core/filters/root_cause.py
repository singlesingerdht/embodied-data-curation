"""根因关联分析：质检结束后的纯后处理器，把 indicators 综合成"为什么"的原因定位。

两级结构：
- 一级（_aggregate_family_events + _CAUSE_RULES 原因映射表）：按 indicator_code
  聚合**非 pass** 记录，逐族归因（J5 跳变/J7 冻结/J2 burst/C5/C9/M6/M10/C7 掉帧等），
  每族产一条 *_anomaly 事件，sources 回指贡献记录（point_index=None=指标级）；
- 二级（保留旧逻辑）：C1 族掉帧 points 的跨通道聚类（correlated_gap）与周期性判定
  （periodic_gaps）、M6 稳定性 detail 的漂移/跳变归因（drift_attributed）。

产出 RootCause（verdict + events），每个事件带 sources 关联回贡献的 indicators
点位/记录。写入 qc-2.2 输出的顶层 root_cause 节点。

缺口判定直接复用 C1 已检测的 gaps（口径与 C1 一致：drop_gap_multiplier×期望周期
+ drop_gap_min_ms），消除了旧实现用中位间隔阈值（max(2*med, GAP_MIN_MS)）产生的
两套口径分歧；root_cause 不再消费流水线内部数据，可独立测试。

典型价值（实测 p4-493）：8 通道在 t≈4.04s 同步缺口 155ms → 自动标出系统级停顿；
kuavo_arm_traj 缺口每 ~50ms 规律出现 → 自动标出周期性带宽竞争；相机 est_freq 正常
且无漂移 → 归因"大面积缺帧而非时钟错误"。
"""
from typing import List

import numpy as np

from ..report.model import Ref, RootCause, RootCauseEvent

CORR_WINDOW_MS: float = 100.0   # 跨通道对齐窗口（±）
CORR_MIN_FAMILIES: int = 3      # 至少几个**传感器族**同时缺口才算系统级事件
PERIOD_MIN_GAPS: int = 8        # 周期性判定最少缺口数
PERIOD_CV_MAX: float = 0.35     # 缺口间隔 CV 上限（低于才判为规则周期）

# C1 族 code 集合（掉帧/缺帧，各模态注册表统一语义）
_DROP_CODES = {"C1", "I1", "J1", "A1"}

_VERDICT_MAX_CHARS = 100   # verdict 人读概要硬上限（字符）；细节在 events[]/detail 全量保留


def _compose_verdict(lines: list) -> str:
    """拼接归因语句并压到 _VERDICT_MAX_CHARS 内（只压缩人读概要，不动 events）。

    超限策略：按现有顺序（error 族/系统级在前，已由调用方排序）逐条累计，
    装不下的语句丢弃，最后一条截断补省略号——绝不出现裸截断（截断只发生在句末）。
    """
    text = "；".join(lines)
    if len(text) <= _VERDICT_MAX_CHARS:
        return text
    acc = ""
    for ln in lines:
        piece = ln if not acc else "；" + ln
        if len(acc) + len(piece) > _VERDICT_MAX_CHARS:
            # 为末尾省略号留 1 字符：可容纳的剩余正文 = 上限-1-已用(-分隔符)
            k = _VERDICT_MAX_CHARS - 1 - len(acc) - (1 if acc else 0)
            if k > 0:
                acc = (acc + "；" if acc else "") + ln[:k]
            return acc + "…"
        acc += piece
    return acc


def _sensor_family(topic: str) -> str:
    from . import topic_semantics
    return topic_semantics.sensor_family(topic)


# ===================== 一级：逐 code 族原因映射表 =====================
# 每个非 pass 的 indicator_code 族 → 归因函数（输入该族全部非 pass 记录，
# 输出 (cause_text, detail_extra)）。evidence 读各族 detail 的强类型字段，
# 措辞用"疑似/可能"（证据化归因，不坐实根因）。
_SEV_RANK = {"pass": 0, "warning": 1, "error": 2}


def _as_detail(r) -> dict:
    return r.detail if isinstance(r.detail, dict) else {}


def _rec_count(r) -> int:
    """该记录代表的问题次数（族感知：M6 用 anomaly_count、M10 用 segments…）。"""
    d = _as_detail(r)
    c = r.indicator_code
    if c == "M6":
        return d.get("anomaly_count", d.get("topic_count", 0))
    if c == "M10":
        return d.get("segments", 0)
    if c == "C7":
        return len(d.get("asymmetries", []) or [])
    return d.get("count", 0)


def _drop_rule(records):
    worst = max(records, key=lambda r: _as_detail(r).get("drop_ratio") or 0.0)
    d = _as_detail(worst)
    ratio = d.get("drop_ratio") or 0.0
    cause = (f"通道 {worst.topic_name} 掉帧率 {ratio * 100:.1f}%"
             f" → 疑似传输/驱动丢帧")
    return cause, {}


def _jump_rule(records):
    total = sum(_rec_count(r) for r in records)
    cause = f"{total} 次 NaN/越界/跳变 → 疑似编码器毛刺"
    return cause, {}


def _freeze_rule(records):
    total = sum(_rec_count(r) for r in records)
    cause = f"{total} 段数据冻结 → 疑似传感器流中断"
    return cause, {}


def _jitter_rule(records):
    worst = max(records, key=lambda r: _as_detail(r).get("cv") or 0.0)
    d = _as_detail(worst)
    cause = f"帧间隔不稳（CV={d.get('cv', 0.0):.4g}）→ 疑似调度抖动"
    return cause, {}


def _image_black_rule(records):
    total = sum(_rec_count(r) for r in records)
    cause = f"{total} 帧黑屏/白屏/花屏 → 疑似曝光/采集链路故障"
    return cause, {}


def _image_blur_rule(records):
    worst = min(records, key=lambda r: (_as_detail(r).get("detail") or {}).get("sharpness_median")
                or float("inf"))
    cause = "图像清晰度低于阈值 → 疑似失焦/码率不足"
    return cause, {}


def _m6_rule(records):
    d = _as_detail(records[0])
    cause = f"{d.get('anomaly_count', 0)} 通道时间戳异常 → 疑似时钟抖动/漂移"
    return cause, {}


def _m10_rule(records):
    seg = _as_detail(records[0]).get("segments", 0)
    cause = f"{seg} 个切段点 → 疑似重启/复位"
    return cause, {}


def _c7_rule(records):
    cause = "相机配套不对称 → 疑似通道配置缺失"
    return cause, {}


def _generic_rule(records):
    sev = max(r.level for r in records)
    cause = f"{records[0].indicator_code} 检出 {len(records)} 条异常（{sev}）"
    return cause, {}


_CAUSE_RULES: dict = {}
for _c in ("J5", "A5", "I5"):
    _CAUSE_RULES[_c] = _jump_rule
for _c in ("J7", "A7", "I7", "C10"):
    _CAUSE_RULES[_c] = _freeze_rule
for _c in ("J2", "A2", "C2", "I2"):
    _CAUSE_RULES[_c] = _jitter_rule
for _c in _DROP_CODES:
    _CAUSE_RULES[_c] = _drop_rule
_CAUSE_RULES["C5"] = _image_black_rule
_CAUSE_RULES["C9"] = _image_blur_rule
_CAUSE_RULES["M6"] = _m6_rule
_CAUSE_RULES["M10"] = _m10_rule
_CAUSE_RULES["C7"] = _c7_rule


def _aggregate_family_events(indicators):
    """一级：按 indicator_code 聚合非 pass 记录，逐族归因。

    每组产一条 *_anomaly 事件：detail 含族汇总（code/name/severity/count/topics），
    sources 回指每条贡献记录（point_index=None=指标级）。
    """
    families = {}
    for z in indicators:
        if _SEV_RANK.get(z.level, 0) < 1:
            continue
        families.setdefault(z.indicator_code, []).append(z)

    events: List[RootCauseEvent] = []
    lines = []  # (sev_rank, text)
    for code, records in sorted(families.items()):
        sev = max(records, key=lambda r: _SEV_RANK.get(r.level, 0)).level
        cause, extra = _CAUSE_RULES.get(code, _generic_rule)(records)
        topics = [{"topic": r.topic_name, "severity": r.level,   # 全局指标 topic 为空串，消费端按 scope 判断
                   "count": _rec_count(r)} for r in records]
        detail = {"code": code, "name": records[0].name, "severity": sev,
                  "topic_count": len(records), "count": sum(_rec_count(r) for r in records),
                  "topics": topics}
        if extra:
            detail.update(extra)
        events.append(RootCauseEvent(
            kind=f"{code.lower()}_anomaly", time=None, detail=detail,
            sources=[Ref(indicator_code=code, topic_name=r.topic_name, point_index=None)
                     for r in records]))
        lines.append((_SEV_RANK[sev], cause))
    lines.sort(key=lambda x: -x[0])
    return events, [t for _, t in lines]


def analyze_root_cause_from_indicators(indicators) -> RootCause:
    """基于 indicators 明细的两级根因关联分析（纯后处理器）。

    - 一级：按 indicator_code 聚合非 pass 记录，逐族归因（_CAUSE_RULES 原因映射表），
      每个非 pass 族产一条 *_anomaly 事件，sources 回指贡献记录；
    - 二级：跨通道关联/模式判定（C1 缺口聚类系统级停顿、周期性缺口、M6 漂移/跳变归因）。

    indicators: List["IndicatorResult"]。no_points=True 时 points 缺失，二级的跨通道
    聚类与周期性判定退化，一级（detail 级）归因不受影响。
    """
    events: List[RootCauseEvent] = []
    verdict: List[str] = []

    # ---- 一级：逐 code 族归因（非 pass）----
    fam_events, fam_lines = _aggregate_family_events(indicators)

    # ---- 二级：收集 C1 掉帧点位（gap: topic, start, duration_ms, point_index）----
    c1 = [z for z in indicators if z.indicator_code in _DROP_CODES and isinstance(z.points, list)]
    gaps = []  # (start_sec, duration_ms, topic_name, indicator_code, point_index)
    for z in c1:
        for i, p in enumerate(z.points):
            if isinstance(p, dict) and p.get("start") is not None:
                gaps.append((float(p["start"]), float(p.get("duration_ms") or 0.0),
                             z.topic_name, z.indicator_code, i))
    gaps.sort(key=lambda g: g[0])

    # ---- 跨通道缺口对齐（系统级停顿）----
    cluster_events: List[RootCauseEvent] = []
    i = 0
    while i < len(gaps):
        j = i
        while j + 1 < len(gaps) and (gaps[j + 1][0] - gaps[i][0]) * 1000.0 <= CORR_WINDOW_MS:
            j += 1
        cluster = gaps[i:j + 1]
        fams = sorted({_sensor_family(c[2]) for c in cluster})
        if len(fams) >= CORR_MIN_FAMILIES:
            cluster_events.append(RootCauseEvent(
                kind="correlated_gap",
                time=round(float(np.median([c[0] for c in cluster])), 3),
                detail={"duration_ms": round(max(c[1] for c in cluster), 1),
                        "families": fams, "gap_count": len(cluster)},
                sources=[Ref(indicator_code=c[3], topic_name=c[2], point_index=c[4]) for c in cluster],
            ))
        i = j + 1
    cluster_events.sort(key=lambda e: e.time if e.time is not None else 0.0)
    events += cluster_events
    if cluster_events:
        verdict.append(
            f"{len(cluster_events)} 次系统级停顿（≥{CORR_MIN_FAMILIES} 族同时缺帧，"
            f"最大 {max(e.detail['duration_ms'] for e in cluster_events):.0f}ms）")

    # ---- 周期性缺口（链路/调度带宽竞争）----
    per_topic = []  # (topic, interval_ms, gap_count, indicator)
    for z in c1:
        times = np.array([float(p["start"]) for p in z.points
                          if isinstance(p, dict) and p.get("start") is not None])
        if len(times) < PERIOD_MIN_GAPS:
            continue
        iv = np.diff(times) * 1000.0
        if len(iv) >= 3:
            ivm = float(np.median(iv))
            if ivm > 0 and float(np.std(iv)) / ivm < PERIOD_CV_MAX:
                per_topic.append((z.topic_name, ivm, len(times), z))
    if per_topic:
        topic, ivm, ngaps, z = max(per_topic, key=lambda x: x[2])
        events.append(RootCauseEvent(
            kind="periodic_gaps",
            time=round(float(z.points[0]["start"]), 3),
            detail={"topic": topic, "interval_ms": round(ivm, 1), "gaps": ngaps},
            sources=[Ref(indicator_code=z.indicator_code, topic_name=z.topic_name, point_index=k)
                     for k, p in enumerate(z.points) if isinstance(p, dict)],
        ))
        verdict.append(
            f"{topic} 周期性缺口（约 {ivm:.0f}ms/次）→ 疑似带宽竞争")

    # ---- 漂移/跳变归因（M6）----
    m6 = next((z for z in indicators if z.indicator_code == "M6" and isinstance(z.detail, dict)), None)
    if m6 is not None:
        det = m6.detail
        idx_by_topic = {p.get("topic"): k for k, p in enumerate(m6.points or [])
                        if isinstance(p, dict)}
        drifts = det.get("drift_topics") or []
        jumps = det.get("jump_topics") or []
        for tp in drifts + jumps:
            _mode = tp.get("mode", "drift")
            _topic = tp.get("topic") or ""
            events.append(RootCauseEvent(
                kind="drift_attributed",
                detail={"mode": _mode, "topic": _topic,
                        "drift_rate_ms_per_min": tp.get("drift_rate_ms_per_min")},
                # sources 契约：指向已存在的 (indicator_code, topic_name) 记录；M6 为全局指标
                # topic_name=""，通道名在 detail.topic，point_index 索引 M6.points
                sources=[Ref(indicator_code="M6", topic_name=m6.topic_name,
                             point_index=idx_by_topic.get(_topic))],
            ))
        if drifts:
            verdict.append(f"{len(drifts)} 通道线性漂移")
        if jumps:
            verdict.append(f"{len(jumps)} 通道时钟跳变")

    # ---- 一级逐族归因并入（系统级之后；error 族优先已在 fam_lines 内排序）----
    events += fam_events
    verdict.extend(fam_lines)

    if not verdict:
        verdict.append("未检出明显异常")
    return RootCause(verdict=_compose_verdict(verdict), events=events)
