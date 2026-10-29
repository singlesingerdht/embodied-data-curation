"""检测结果报告：事件 episode 标注 + 落盘单文件 JSON"""

import json
from pathlib import Path
from typing import List, Optional

from .model import (  # noqa: F401  输出契约类型（re-export 兼容旧 import 路径）
    IndicatorResult, FileInfo, Thresholds, ConfigInfo, StageError, RunInfo,
    Ref, RootCauseEvent, RootCause, QualityResult, IndicatorDetail, Point,
)


def annotate_lerobot_episodes(result, info):
    """给内容/时序事件标注所属 episode 文件名。

    用全局帧索引/时间映射到 episode（数据集内第几个 episode_XXXX）。
    """
    import bisect
    from pathlib import Path as _P
    eps = info.extra.get("episodes", [])
    if not eps:
        return
    starts = []
    cum = 0
    for ep in eps:
        starts.append(cum)
        cum += ep.get("rows", 0)
    ts = next((t.timestamps for t in info.topics.values() if t.timestamps), None)

    def ep_of_frame(idx):
        i = bisect.bisect_right(starts, idx) - 1
        return _P(eps[i].get("file") or "").name if 0 <= i < len(eps) else None

    time_starts = []
    if ts is not None:
        cum = 0
        for ep in eps:
            time_starts.append(float(ts[cum]) if cum < len(ts) else None)
            cum += ep.get("rows", 0)

    def ep_of_time(t):
        if not time_starts:
            return None
        valid = [x for x in time_starts if x is not None]
        i = bisect.bisect_right(valid, t) - 1
        if i < 0 or i >= len(eps):
            return None
        if i + 1 < len(eps) and time_starts[i + 1] is not None \
                and abs(t - time_starts[i + 1]) < 1e-6:
            return _P(eps[i + 1].get("file") or "").name
        return _P(eps[i].get("file") or "").name

    for c in result.get("content", {}).get("checks", []):
        for e in c.get("events", []):
            if e.get("index") is not None:
                e["episode"] = ep_of_frame(e["index"])
            elif e.get("time") is not None:
                e["episode"] = ep_of_time(e["time"])
    for _topic, d in (result.get("timing") or {}).items():
        if not isinstance(d, dict):
            continue
        for e in d.get("events_timeline", []):
            t = e.get("time") or e.get("start_time")
            if t is not None:
                e["episode"] = ep_of_time(t)


def save_full_report(result, output_dir, bag_name, ts=None, gzip: bool = False):
    """落盘完整质检报告 output/<名>_qc_<时间戳>.json

    单一文件包含 structure/timing/sync/dedup/content/episode_boundaries
    全部检测段，事件带 range（帧起终点）与 start_time/end_time（秒）。

    默认去缩进（minified，仍是合法 JSON，任何解析器可直接读，体积 -50%+）；
    gzip=True：输出 <名>_qc_<ts>.json.gz（gzip 无损，读写端 gunzip 后即原 JSON；内部同样 minified）。
    """
    from datetime import datetime
    import gzip as _gzip
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = ts or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    data = json.dumps(result, ensure_ascii=False,
                      indent=None, separators=(",", ":"),
                      default=str)
    if gzip:
        fp = out / f"{bag_name}_qc_{ts}.json.gz"
        with _gzip.open(fp, "wt", encoding="utf-8", compresslevel=9) as fh:
            fh.write(data)
    else:
        fp = out / f"{bag_name}_qc_{ts}.json"
        fp.write_text(data, encoding="utf-8")
    return str(fp)


def _iter_events(result):
    """遍历 result 中全部事件（content/timing/sync/structure）"""
    for c in (result.get("content") or {}).get("checks", []):
        for e in c.get("events", []):
            yield e
    tm = result.get("timing")
    if isinstance(tm, dict):
        for d in tm.values():
            if isinstance(d, dict):
                for e in d.get("events_timeline", []):
                    yield e
    sy = result.get("sync")
    if isinstance(sy, dict):
        for e in sy.get("events_timeline", []):
            yield e
    for e in (result.get("structure") or {}).get("events", []):
        yield e


def _check_topic(check_name: str) -> str:
    """content check 名 -> 通道名（content_image_/cam_h -> /cam_h）"""
    for pfx in ("content_image_pixels_", "content_image_", "content_imu_bias_",
                "content_imu_", "content_joint_", "content_cmd_",
                "content_pose_", "content_numeric"):
        if check_name.startswith(pfx):
            return check_name[len(pfx):]
    return check_name


def _file_size(path) -> Optional[int]:
    try:
        p = Path(path)
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return p.stat().st_size
    except Exception:
        return None


def _manufacturer(result) -> str:
    prof = result.get("profile") or {}
    note = str(prof.get("note", "") or "")
    return note.split("；")[0].split(";")[0][:40]


def _topic_type(name: str, ti) -> str:
    msg = (ti or {}).get("msg_type", "") if isinstance(ti, dict) else ""
    if "camera" in msg.lower() or "image" in msg.lower() or "compressed" in msg.lower() \
            or "cam_" in name.lower():
        return "cam"
    if "joint" in msg.lower() or "joint" in name.lower() or "state" in name.lower():
        return "joint"
    if "imu" in msg.lower() or "imu" in name.lower():
        return "imu"
    if "cmd" in name.lower() or "traj" in name.lower() or "action" in name.lower() or "command" in msg.lower():
        return "cmd"
    if "pose" in name.lower() or "odom" in name.lower():
        return "pose"
    return "other"


from dataclasses import dataclass, field as _field


def _base_indicator(td: dict, level: str, name: str, topic: str, code: str = "",
                    check: str = "", modality: str = "") -> IndicatorResult:
    """IndicatorResult 公共字段（时序类带 valid/duration/freq，其余为空）"""
    return IndicatorResult(
        topic_name=topic, scope="topic", check=check, indicator_code=code,
        modality=modality, name=name,
        valid_count=td.get("valid_count"),
        duration_sec=str(td["duration_sec"]) if td.get("duration_sec") is not None else "",
        estimated_freq=str(td["estimated_freq"]) if td.get("estimated_freq") is not None else "",
        level=level,
    )


def _timing_indicators(td: dict, topic: str, modality: str = "") -> list:
    """时序指标：族语义（drop/jitter/…）按通道模态从注册表落到具体 code。"""
    from ..indicators import Indicator, Modality, timing_code
    sub = td.get("sub_severity") or {}
    _mod = {"image": Modality.CAMERA, "imu": Modality.IMU,
            "joint_state": Modality.JOINT, "cmd": Modality.CMD,
            "pose": Modality.POSE}.get(modality)
    # 表内映射：语义 -> (族语义或具体 code)。表外自建指标（silence 等）挂到最贴近的表内族
    sem_to_code = {
        "drop": timing_code(_mod, "drop") if _mod else "C1",
        "jitter": timing_code(_mod, "jitter") if _mod else "C2",
        "out_of_order": timing_code(_mod, "out_of_order") if _mod else "C3",
        "timestamp_duplicate": timing_code(_mod, "duplicate") if _mod else "C4",
        "silence": timing_code(_mod, "drop") if _mod else "C1",
        "frequency_deviation": timing_code(_mod, "jitter") if _mod else "C2",
        "timestamp_validity": "C8",
        "delay": "M5",
    }
    detail_map = {"drop": "drop_detail", "jitter": "jitter_detail",
                  "out_of_order": "out_of_order_detail", "delay": "delay_detail"}
    point_map = {"drop": "gaps", "out_of_order": "positions"}
    out = []
    for key, sev in sub.items():
        code = sem_to_code.get(key, "")
        try:
            ind = Indicator[code]
            name = ind.name_zh
        except KeyError:
            name = key
        item = _base_indicator(td, sev, name, topic, code=code, check=key,
                               modality=modality)
        dk = detail_map.get(key)
        if dk and td.get(dk):
            det = {k: v for k, v in td[dk].items() if k != point_map.get(key)}
            if det:
                item.detail = det
        pk = point_map.get(key)
        if pk and td.get(dk) and td[dk].get(pk):
            _full = td[dk][pk]
            item.points = _full[:200]   # 与 content 路径一致：points 上限 200/指标
            if len(_full) > 200:
                # 截断显式说明：consumer 可据 points_truncated/points_total 判断为抽样
                item.detail = dict(item.detail or {})
                item.detail["points_truncated"] = True
                item.detail["points_total"] = len(_full)
        out.append(item)
    return out


def _ratio_level(ratio, low: float, high: float):
    """帧占比三档分档（业务规则：轻度算 pass）。>low → warning；≥high → error。
    ratio 为 None（占比不可得）→ None，调用方回退事件档。"""
    if ratio is None:
        return None
    if ratio >= high:
        return "error"
    if ratio > low:
        return "warning"
    return "pass"


def _image_frame_ratios(c) -> tuple:
    """取图像检查 detail 的坏帧/模糊帧占比（全帧模式嵌套在 detail.fullframe）。

    返回 (bad_ratio, blur_ratio)；非图像检查或缺键 → (None, None)，
    C5/C9 指标层回退事件档（现状行为）。
    """
    det = c.get("detail")
    if not isinstance(det, dict):
        return None, None
    src = det.get("fullframe")
    if not isinstance(src, dict):
        src = det
    return src.get("bad_ratio"), src.get("blur_ratio")


# C5/C9 指标层占比分档的例外子规则：下列事件无论如何 ≥ warning
# （整帧黑/白屏与相机级持续低清晰度不被占比稀释；持续性重度段事件本身为
#   error 档，同样保持——见 _content_indicators 内取 max 逻辑）
_C5_EVENT_FLOOR = {"pixel_black", "pixel_white", "image_collapse"}
_C9_EVENT_FLOOR = {"pixel_low_sharpness"}


def _content_indicators(ct, tname: str, content_thresholds: dict = None,
                        modality: str = "") -> list:
    from ..indicators import Indicator, EVENT_TYPE_TO_CODE, FREEZE_PREFIX_TO_CODE
    if not isinstance(ct, dict):
        return []
    # 事件类型 -> 子检查名（yaml 检测项；C5 族按 black_screen/occlusion/garbled/... 拆分）
    _ET2CHECK = {
        "pixel_black": "black_screen", "pixel_white": "black_screen",
        "image_collapse": "black_screen", "pixel_occlusion": "occlusion",
        "pixel_garbled": "garbled", "pixel_overexpo": "overexpo",
        "pixel_underexpo": "underexpo", "pixel_color_anomaly": "color_anomaly",
        "pixel_blur": "blur", "pixel_low_sharpness": "blur",
        "image_resolution_change": "resolution_change",
        "image_encoding_change": "resolution_change",
        "joint_nan": "joint_anomaly", "joint_bounds": "joint_anomaly",
        "joint_jump": "joint_anomaly", "joint_reset": "joint_anomaly",
        "joint_velocity": "joint_anomaly", "cmd_nan": "cmd_anomaly",
        "cmd_bounds": "cmd_anomaly", "cmd_jerk": "cmd_anomaly",
        "imu_nan": "imu_anomaly", "imu_bounds": "imu_anomaly",
        "bias_suspect": "bias",
        "pose_nan": "pose_anomaly", "pose_jerk": "pose_jerk",
        "pose_reset": "pose_reset",
    }
    _FREEZE2CHECK = {"C10": "freeze", "J7": "joint_freeze",
                     "I7": "imu_freeze", "A7": "cmd_freeze",
                     "P7": "pose_freeze"}
    # C5/C9 占比分档阈值（来自 profile/defaults 生效值；缺省与 defaults.yaml 一致）
    _thr = content_thresholds or {}
    _bad_lo = _thr.get("bad_frame_low", 0.05)
    _bad_hi = _thr.get("bad_frame_high", 0.3)
    _blur_lo = _thr.get("blur_low", 0.1)
    _blur_hi = _thr.get("blur_high", 0.3)
    merged = {}
    for c in ct.get("checks", []):
        cname = c.get("name", "")
        if cname == "content_numeric":
            continue
        if _check_topic(cname) != tname:
            continue
        # 本检查的帧占比（非图像检查为 None → C5/C9 事件回退事件档）
        _bad_ratio, _blur_ratio = _image_frame_ratios(c)
        for e in c.get("events", []):
            et = e.get("type", "")
            code = EVENT_TYPE_TO_CODE.get(et, "")
            if et == "data_freeze":
                # 冻结按检查名前缀区分模态（content_imu_/joint_/cmd_/image_）
                for pfx, fc in FREEZE_PREFIX_TO_CODE.items():
                    if cname.startswith("content_" + pfx[len("content_"):]):
                        code = fc
                        break
            if not code:
                continue
            check = _ET2CHECK.get(et) or _FREEZE2CHECK.get(code, "")
            try:
                sem = Indicator[code].name_zh
            except KeyError:
                continue
            key = (tname, code, check)
            sev = e.get("severity", "warning")
            # C5/C9 指标档 = 帧占比分档（轻度算 pass）+ 例外子规则：
            # - 整帧黑/白屏（C5）/相机级持续低清晰度（C9）事件 → ≥ warning
            # - error 档事件（如全帧管线 ≥5s 持续段）→ 保持 error
            # - 占比不可得（如深度图跳过全帧分析）→ 回退事件档（原行为）
            _lvl = None
            if code == "C5":
                _lvl = _ratio_level(_bad_ratio, _bad_lo, _bad_hi)
                if et in _C5_EVENT_FLOOR and (_lvl is None or _sev_rank[_lvl] < 1):
                    _lvl = "warning"
            elif code == "C9":
                _lvl = _ratio_level(_blur_ratio, _blur_lo, _blur_hi)
                if et in _C9_EVENT_FLOOR and (_lvl is None or _sev_rank[_lvl] < 1):
                    _lvl = "warning"
            if _lvl is not None:
                sev = _lvl
                if _sev_rank.get(e.get("severity", "pass"), 0) >= 2:
                    sev = "error"
            rng = e.get("range") or [e.get("index"), e.get("index")]
            pts = {"index": e.get("index"), "range": rng}
            if e.get("time") is not None:
                pts["time"] = e["time"]
            if e.get("episode"):
                pts["episode"] = e["episode"]
            if key not in merged:
                _det = e.get("detail", {})
                if _lvl is not None:
                    # 挂占比证据（拷贝防污染事件原 detail），消费端可见分档依据
                    _det = dict(_det)
                    if code == "C5" and _bad_ratio is not None:
                        _det["bad_ratio"] = _bad_ratio
                    if code == "C9" and _blur_ratio is not None:
                        _det["blur_ratio"] = _blur_ratio
                merged[key] = {"code": code, "check": check, "name": sem, "level": sev,
                               "count": 1, "points": [pts], "detail": _det}
            else:
                merged[key]["count"] += 1
                if _sev_rank.get(sev, 0) > _sev_rank.get(merged[key]["level"], 0):
                    merged[key]["level"] = sev
                if len(merged[key]["points"]) < 200:
                    merged[key]["points"].append(pts)
    # 深度质检证据图（evidence.py 提取，仅 deep 产出）：{check: [相对路径]}
    ev_map = (ct.get("_evidence") or {}).get(tname, {}) if isinstance(ct, dict) else {}
    out = []
    for (tname2, code, check), v in merged.items():
        _extra = {"count": v["count"], "detail": v["detail"]}
        _ev = ev_map.get(check)
        if _ev:
            _extra["evidence"] = _ev
        if v["count"] > 200:
            # points 截断显式说明（count 为事件总数，points 仅前 200 条）
            _extra["points_truncated"] = True
            _extra["points_total"] = v["count"]
        out.append(IndicatorResult(topic_name=tname2, scope="topic", check=check,
                                   indicator_code=code, modality=modality,
                                   name=v["name"], valid_count=None,
                                   duration_sec="", estimated_freq="", level=v["level"],
                                   detail=_extra,
                                   points=v["points"]))
    return out


def _structure_indicators(st, modality_map=None) -> list:
    from ..indicators import Indicator
    if not isinstance(st, dict):
        return []
    _mm = modality_map or {}
    # 检查名 -> 设计表 code（名称从注册表取；表外检查挂最贴近的表内项）
    name_map = {"file_integrity": "M8", "topic_completeness": "M3",
                "lerobot_integrity": "M8", "camera_info_pairing": "C7",
                "camera_symmetry": "C7", "rgbd_pairing": "C7",
                "metadata_completeness": "M8", "format_recognition": "M8"}
    out = []
    for c in st.get("checks", []):
        cname = c.get("name", "")
        aff = c.get("affected_topics") or []
        if cname.startswith("schema_"):
            code, name, check = "M7", Indicator.M7.name_zh, "schema_stability"
            topic = aff[0] if aff else ""
        else:
            code = name_map.get(cname, "")
            check = cname   # 子检查名=结构检查名（file_integrity/format_recognition/...）
            try:
                name = Indicator[code].name_zh if code else cname
            except KeyError:
                name = cname
            topic = ""
        # scope 与 topic_name 保持一致：有归属通道=单通道，否则全局（结构/文件级）
        scope = "topic" if topic else "global"
        out.append(IndicatorResult(topic_name=topic, scope=scope, check=check,
                                   indicator_code=code, modality=_mm.get(topic, ""),
                                   name=name, valid_count=None,
                                   duration_sec="", estimated_freq="",
                                   level=c.get("severity", "pass"),
                                   detail=c.get("detail", {})))
    return out


def _cross_topic_indicators(result, sy) -> list:
    from ..indicators import Indicator
    out = []
    if isinstance(sy, dict):
        cov = sy.get("coverage")
        if isinstance(cov, dict):
            out.append(IndicatorResult(topic_name="", scope="global", check="coverage", indicator_code="M2",
                                       name=Indicator.M2.name_zh, valid_count=None,
                                       duration_sec="", estimated_freq="", level=cov.get("severity", "pass"),
                                       detail={"coverage_ratio": cov.get("coverage_ratio"),
                                               "late_start": len(cov.get("late_start_topics", [])),
                                               "early_end": len(cov.get("early_end_topics", []))}))
        al = sy.get("alignments")
        if isinstance(al, dict) and al:
            worst = max((a.get("severity", "pass") for a in al.values() if isinstance(a, dict)),
                        key=lambda s: _sev_rank.get(s, 0)) if al else "pass"
            pts = [{"topic": k, "p95_delta_ms": v.get("p95_delta_ms"), "severity": v.get("severity")}
                   for k, v in al.items() if isinstance(v, dict)]
            out.append(IndicatorResult(topic_name="", scope="global", check="alignment", indicator_code="M1",
                                       name=Indicator.M1.name_zh, valid_count=None,
                                       duration_sec="", estimated_freq="", level=worst,
                                       detail={"topic_pairs": len(al)}, points=pts))
        st = sy.get("stability")
        if isinstance(st, dict) and st:
            worst = max((s.get("severity", "pass") for s in st.values() if isinstance(s, dict)),
                        key=lambda x: _sev_rank.get(x, 0)) if st else "pass"
            pts = [{"topic": k, "mode": v.get("mode"), "drift_rate_ms_per_min": v.get("drift_rate_ms_per_min")}
                   for k, v in st.items() if isinstance(v, dict)]
            # mode 语义：drift=线性漂移率超标(>5/20ms/min)；jump=时钟电平跳变；unstable=抖动 std 超阈但无线性漂移
            drift_topics = [p for p in pts if p.get("mode") == "drift"]
            jump_topics = [p for p in pts if p.get("mode") == "jump"]
            unstable_topics = [p for p in pts if p.get("mode") == "unstable"]
            anomaly_topics = drift_topics + jump_topics + unstable_topics
            out.append(IndicatorResult(topic_name="", scope="global", check="stability", indicator_code="M6",
                                       name=Indicator.M6.name_zh, valid_count=None,
                                       duration_sec="", estimated_freq="", level=worst,
                                       detail={"topic_count": len(st),
                                               "drift_count": len(drift_topics),
                                               "jump_count": len(jump_topics),
                                               "unstable_count": len(unstable_topics),
                                               "anomaly_count": len(anomaly_topics),
                                               "drift_topics": drift_topics,
                                               "jump_topics": jump_topics,
                                               "unstable_topics": unstable_topics,
                                               "anomaly_topics": anomaly_topics}, points=pts))
    eb = result.get("episode_boundaries")
    if isinstance(eb, list) and eb:
        # points 与其它指标一致设 200 上限（切段点可成千上万：全坏数据集持续多通道异常时
        # 每 200ms 窗口都满足 >=3 topic，segments 计总数、points 截断防输出膨胀）
        out.append(IndicatorResult(topic_name="", scope="global", check="episode_consistency", indicator_code="M10",
                                   name=Indicator.M10.name_zh, valid_count=None,
                                   duration_sec="", estimated_freq="",
                                   level="warning" if eb else "pass",
                                   detail={"segments": len(eb),
                                           "points_truncated": len(eb) > 200,
                                           "points_total": len(eb)},
                                   points=[{"time": e.get("time"), "topics": e.get("topics")}
                                           for e in eb][:200]))
    return out


_sev_rank = {"pass": 0, "warning": 1, "error": 2}


def to_topic_structure(result, no_points: bool = False) -> QualityResult:
    """把内部 result 组织为 QualityResult 对象模型（业务端类层次 qc-2.2）：
    file_info / sensors / config / run / root_cause + indicators（List[IndicatorResult]，默认全量）。

    root_cause 由流水线 _assemble 在组装后附着（质检后处理器产出），此处为默认空对象。
    """
    st = result.get("structure") or {}
    tm = result.get("timing") or {}
    ct = result.get("content") or {}
    sy = result.get("sync") or {}
    topics_info = st.get("topics") or {}
    sensors = sorted(topics_info.keys())

    indicators = []
    # 阶段级异常（timing/content 整段失败）：伪指标不进 indicators（不污染 code 命名空间），
    # 记入 run.errors；消费端可据此区分"检了无问题"与"阶段没跑成"
    stage_errors = []
    if isinstance(tm, dict) and tm.get("error"):
        stage_errors.append(StageError(stage="timing", error=tm["error"]))
    if isinstance(ct, dict) and ct.get("error"):
        stage_errors.append(StageError(stage="content", error=ct["error"]))
    from ..indicators import Indicator, Modality, family
    modality_map = result.get("_topic_modality") or {}
    indicators += _structure_indicators(st, modality_map)
    for tname in sensors:
        td = tm.get(tname)
        _mod = modality_map.get(tname, "")
        if isinstance(td, dict):
            indicators += _timing_indicators(td, tname, _mod)
        indicators += _content_indicators(
            ct, tname,
            ((result.get("_config") or {}).get("thresholds") or {}).get("content"),
            _mod)
        _dd = (result.get("dedup") or {}).get(tname)
        if isinstance(_dd, dict):
            _modem = {"image": Modality.CAMERA, "imu": Modality.IMU,
                      "joint_state": Modality.JOINT, "cmd": Modality.CMD,
                      "pose": Modality.POSE}.get(_mod)
            _dup = family(_modem, "重复消息/帧") if _modem else None
            indicators.append(IndicatorResult(topic_name=tname, scope="topic",
                                              indicator_code=_dup.code if _dup else "C4",
                                              modality=_mod, check="duplicate",
                                              name=_dup.name_zh if _dup else "重复消息/帧",
                                              valid_count=None, duration_sec="", estimated_freq="",
                                              level=_dd.get("severity", "pass"),
                                              detail={"duplicate_ratio": _dd.get("duplicate_ratio"),
                                                      "duplicate_count": _dd.get("duplicate_count")}))
    indicators += _cross_topic_indicators(result, sy)

    # level 归一：仅三档 pass/warning/error（dedup/content 的 info 等降为 pass，与业务端一致）
    for _z in indicators:
        if _z.level not in _sev_rank:
            _z.level = "pass"

    # no_points：丢逐事件点位明细（points 数组，占体积大头），保留 detail 汇总
    if no_points:
        for z in indicators:
            z.points = None   # to_dict 时省略该键

    _cfg = result.get("_config") or {"frequencies": {}, "thresholds": {}}
    return QualityResult(
        schema_version="qc-2.2",
        file_info=FileInfo(path=result.get("file"), format=result.get("format"),
                           size_bytes=_file_size(result.get("file")),
                           manufacturer=_manufacturer(result)),
        sensors=sensors,
        config=ConfigInfo(frequencies=_cfg.get("frequencies", {}),
                          thresholds=Thresholds(**_cfg.get("thresholds", {}))),
        run=RunInfo(mode=result.get("mode") or "",
                    skipped_indicators=result.get("skipped_indicators") or [],
                    errors=stage_errors,
                    event_count=sum(1 for _ in _iter_events(result)),
                    elapsed_sec=result.get("elapsed_sec", {}).get("total")),
        indicators=indicators,
    )
