"""深度质检证据图像提取（evidence capture，方案 A：路径回传）。

仅 deep（unpack=True）模式启用：content 检出的图像族事件（C5 黑屏/白屏/花屏/
遮挡、C9 模糊、C10 冻结、C8 分辨率/编码变化）在收尾阶段按帧索引重新解码像素
（reader.image_pixels，reader 契约现成接口，全部 reader 实现），PIL 编码 JPEG
落盘 <output>/images/<数据集名>/<topic>/<check>_<index>.jpg——与质检结果 JSON
报告**同级目录**（输出目录挂载后业务端一个目录取全），并把相对路径回写
indicators[].detail.evidence（fast 模式不提取）。

只提取不改检测：事件已带 index/range；解码/落盘失败的帧跳过记日志，不中断。
"""
import logging
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger("edc.algorithms.quality.indicator_core.evidence")

# 图像族事件类型（C5/C9/C10/C8）→ 子检查名（与 report._ET2CHECK 一致，用于落盘目录）
_ET2CHECK = {
    "pixel_black": "black_screen", "pixel_white": "black_screen",
    "image_collapse": "black_screen", "pixel_occlusion": "occlusion",
    "pixel_garbled": "garbled", "pixel_overexpo": "overexpo",
    "pixel_underexpo": "underexpo", "pixel_color_anomaly": "color_anomaly",
    "pixel_blur": "blur", "pixel_low_sharpness": "blur", "blur_suspect": "blur",
    "data_freeze": "freeze",
    "image_resolution_change": "resolution_change",
    "image_encoding_change": "resolution_change",
}

EVIDENCE_MAX_FRAMES = 20   # 每文件证据帧上限（error 优先）
JPEG_QUALITY = 85
_SEV_RANK = {"pass": 0, "warning": 1, "error": 2}


def _safe(name: str) -> str:
    """topic/数据集名 → 安全目录名（/ 清洗，防路径穿越）。"""
    n = str(name or "").strip().replace("/", "_").replace("\\", "_")
    return n or "unknown"


def _frame_index(e: dict) -> int:
    """事件取帧策略：单点取 index；段事件（range=[a,b], b>a）取 start 帧
    （黑屏段起点即黑、冻结段起点即冻结）；index=-1（相机级整体事件）→ -1 不可取帧。"""
    idx = e.get("index")
    if idx is None or idx < 0:
        return -1
    det = e.get("detail") or {}
    rng = det.get("range") if isinstance(det, dict) else None
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        a, b = rng
        if a is not None and b is not None and b > a:
            return int(a)
    return int(idx)


def collect_evidence_requests(content_result: dict) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """从 content 结果收集图像族证据请求：{topic: {check: (index, sev_rank)}}。

    **每个指标（topic×check 一条记录）最多 1 帧**：取该指标问题帧中档位最高的
    一帧（同档取首个）；只收集非 pass 图像族事件；index 无效（-1 相机级事件，
    如 pixel_low_sharpness）跳过。
    """
    requests: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for c in (content_result or {}).get("checks", []):
        for e in c.get("events", []):
            check = _ET2CHECK.get(e.get("type", ""))
            if check is None:
                continue
            sev = _SEV_RANK.get(e.get("severity"), 0)
            if sev < 1:
                continue                       # pass 事件不提取
            idx = _frame_index(e)
            if idx < 0:
                continue                       # 相机级整体事件无单帧
            topic = e.get("topic", "")
            cur = requests.setdefault(topic, {}).get(check)
            if cur is None or sev > cur[1]:
                requests[topic][check] = (idx, sev)
    return requests


def extract_evidence(reader, requests: Dict[str, Dict[str, Tuple[int, int]]],
                     output_dir: str, dataset_name: str = "",
                     max_frames: int = EVIDENCE_MAX_FRAMES
                     ) -> Dict[str, Dict[str, list]]:
    """按请求解码落盘证据图；返回 {topic: {check: [相对路径]}}。

    - 每指标（topic×check）1 帧（collect 已聚合）；全局上限 max_frames 帧兜底
      （error 优先）；reader.image_pixels 批量解码；
    - PIL 编码 JPEG 落盘 <output_dir>/images/<数据集>/<topic>/<check>_<idx>.jpg
      （与 JSON 报告同级，输出目录挂载后业务端一个目录取全）；
    - 解码/落盘失败跳过（记日志），不中断。
    """
    if not requests:
        return {}
    from PIL import Image

    flat = [(t, c, i, s) for t, m in requests.items() for c, (i, s) in m.items()]
    flat.sort(key=lambda x: (-x[3], x[2]))     # error 优先，同档按帧序
    flat = flat[:max_frames]

    ds = _safe(dataset_name)
    out: Dict[str, Dict[str, list]] = {}
    by_topic: Dict[str, List[Tuple[str, int]]] = {}
    for t, c, i, _s in flat:
        by_topic.setdefault(t, []).append((c, i))
    for topic, items in by_topic.items():
        try:
            frames = reader.image_pixels(topic, [i for _c, i in items])
        except Exception as exc:
            logger.warning("evidence 解码失败 topic=%s: %s", topic, exc)
            continue
        if not frames:
            continue
        for (t2, check, idx, _s), arr in zip([f for f in flat if f[0] == topic], frames):
            if arr is None:
                continue
            try:
                img = Image.fromarray(arr)
                rel = f"images/{ds}/{_safe(topic)}/{check}_{idx}.jpg"
                _p = Path(output_dir) / rel
                _p.parent.mkdir(parents=True, exist_ok=True)
                img.save(_p, "JPEG", quality=JPEG_QUALITY)
                out.setdefault(t2, {}).setdefault(check, []).append(rel)
            except Exception as exc:
                logger.warning("evidence 落盘失败 topic=%s idx=%s: %s", t2, idx, exc)
    return out
