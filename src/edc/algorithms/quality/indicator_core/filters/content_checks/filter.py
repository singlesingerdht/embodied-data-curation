"""ContentFilter：组合各 mixin 的内容检测过滤器"""
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Literal
from ...data.readers import DataReader
from ...data.ir import (
    MODALITY_IMAGE, MODALITY_IMU, MODALITY_JOINT_STATE, MODALITY_CMD,
    MODALITY_POSE, _NUMERIC_MODALITIES,
)
from .model import CheckResult, ContentEvent, ContentResult, ContentConfig
from .scoring import ScoringMixin
from .freeze import FreezeMixin
from .image import ImageChecksMixin, FullFrameMixin
from .numeric import NumericChecksMixin

logger = logging.getLogger("edc.algorithms.quality.indicator_core.content")

class ContentFilter(ScoringMixin, FreezeMixin, ImageChecksMixin, FullFrameMixin, NumericChecksMixin):
    def __init__(self, config: Optional[ContentConfig] = None):
        self.config = config or ContentConfig()

    def _cfg_for(self, name: str) -> ContentConfig:
        """per-channel 阈值覆盖（profile topics.<名>.filters.content）"""
        ov = self.config.topic_overrides.get(name)
        if ov:
            from dataclasses import replace
            return replace(self.config, **ov)
        return self.config

    def analyze(self, bag_path: str, reader: Optional[DataReader] = None) -> ContentResult:
        """
        对数据源执行所有数值检测

        Args:
            bag_path: 数据文件路径（支持 .bag/.db3/.mcap/.parquet/.h5/.mp4 等）
            reader: 已打开的 DataReader（可选；共享 reader 避免重复打开）

        Returns:
            ContentResult: 检测结果
        """
        path = Path(bag_path)
        checks: List[CheckResult] = []
        all_events: List[ContentEvent] = []

        if not path.exists():
            err = CheckResult(
                name="content_analysis",
                passed=False, severity="error",
                message=f"文件不存在: {bag_path}",
            )
            return ContentResult(
                bag_path=bag_path, checks=[err], events=[],
                overall_severity="error", summary={"error": 1, "warning": 0, "pass": 0},
            )

        created = reader is None
        if created:
            reader = DataReader.create(bag_path)
            try:
                info = reader.open(bag_path)
            except Exception as e:
                err = CheckResult(
                    name="content_analysis",
                    passed=False, severity="error",
                    message=f"无法读取数据: {e}",
                )
                return ContentResult(
                    bag_path=bag_path, checks=[err], events=[],
                    overall_severity="error", summary={"error": 1, "warning": 0, "pass": 0},
                )
        else:
            info = reader.info()

        try:
            # 汇总需要检测的通道
            numeric_ch: List[str] = []
            image_ch: List[str] = []
            px_idx: Dict[str, List[int]] = {}
            for name, ti in info.topics.items():
                if ti.modality == MODALITY_IMAGE and ti.image_available \
                        and self.config.image_layer1_enabled:
                    image_ch.append(name)
                    if self.config.unpack:
                        px_idx[name] = self.sample_positions(ti.count)
                elif (ti.modality in _NUMERIC_MODALITIES or ti.modality == MODALITY_POSE) \
                        and ti.value_available and ti.count >= 3 \
                        and self.config.numeric_enabled:
                    numeric_ch.append(name)

            # 单遍收集（bag 类格式免反复遍历）；解包关闭时不收集数值
            plan = reader.collect_content(
                set(numeric_ch) if self.config.unpack else set(),
                set(image_ch), px_idx)

            # ---- 数值通道（需解包）----
            if numeric_ch and not self.config.unpack:
                checks.append(CheckResult(
                    name="content_numeric",
                    passed=True, severity="pass",
                    message=f"解包已关闭，跳过 {len(numeric_ch)} 个数值通道检测",
                    events=[], detail={"skipped": True, "reason": "unpack_disabled"},
                ))
            for name in numeric_ch:
                if not self.config.unpack:
                    break
                ti = info.topics[name]
                sample_positions = px_idx.get(name) or self.sample_positions(ti.count)
                if plan is not None:
                    vals = plan[name]["values"]
                    samples = [(i, vals[i]) for i in sample_positions if i < len(vals) and vals[i]]
                    full_iter = iter(vals)
                else:
                    samples = []
                    # E9: sampling_full_scan_threshold 接线——无 iter_values 的 reader（如 BVH）
                    # 在小通道时全量采样，避免头/中/尾抽样漏检
                    pos_scan = sample_positions
                    if ti.count <= self.config.sampling_full_scan_threshold:
                        pos_scan = list(range(ti.count))
                    sampled = reader.channel_values(name, pos_scan)
                    if sampled is not None:
                        samples = [(i, d) for i, d in zip(pos_scan, sampled) if d]
                    full_iter = reader.iter_values(name)

                _orig_cfg = self.config
                self.config = self._cfg_for(name)
                try:
                    if ti.modality == MODALITY_IMU:
                        check = self._check_imu(name, ti, samples, full_iter)
                        if self.config.imu_bias_detect:
                            bias_iter = iter(plan[name]["values"]) if plan is not None \
                                else reader.iter_values(name)
                            checks.append(self._check_imu_bias(name, ti, bias_iter))
                    elif ti.modality == MODALITY_JOINT_STATE:
                        check = self._check_joint_state(name, ti, samples, full_iter)
                    elif ti.modality == MODALITY_POSE:
                        check = self._check_pose(name, ti, samples, full_iter)
                    elif ti.modality == MODALITY_CMD:
                        check = self._check_joint_cmd(name, ti, samples, full_iter)
                    else:
                        continue
                    checks.append(check)
                finally:
                    self.config = _orig_cfg

            # ---- 图像通道：先层1（普查），再层2（自适应采样，重点补查层1异常区）----
            image_anomaly: Dict[str, set] = {}
            # 并发预取图像列（parquet 大列 BROTLI 解压慢，多列并发读单次检测即提速；
            # bag/mcap 走 collect_content 单遍，无需预取）
            if len(image_ch) > 1 and hasattr(reader, "preload_image_columns"):
                try:
                    reader.preload_image_columns(image_ch)
                except Exception:
                    pass  # 预取失败回退逐通道读取
            for name in image_ch:
                ti = info.topics[name]
                if plan is not None:
                    frames_it = iter(plan[name]["stats"])
                else:
                    frames_it = reader.image_frames(name)
                _orig_cfg = self.config
                self.config = self._cfg_for(name)
                try:
                    check = self._check_image_compressed(name, ti, frames_it)
                    checks.append(check)
                finally:
                    self.config = _orig_cfg
                # 收集层1异常帧（段事件的起点/中点/终点）
                anomaly = set()
                for e in check.events:
                    if getattr(e, "index", None) is not None:
                        anomaly.add(e.index)
                    rng = (e.detail or {}).get("range") if e.detail else None
                    if rng and rng[0] is not None and rng[1] is not None:
                        a, b = rng[0], rng[1]
                        anomaly.update((a, (a + b) // 2, b))
                image_anomaly[name] = anomaly

            # ---- 全帧管线（unpack + image_fullframe + image_bytes 可用时追加）----
            fullframe_done: set = set()
            if self.config.unpack and self.config.image_fullframe:
                for name in image_ch:
                    it = reader.image_bytes(name)
                    if it is None:
                        continue
                    ti = info.topics[name]
                    _orig_cfg = self.config
                    self.config = self._cfg_for(name)
                    try:
                        new_check = self._check_image_fullframe(name, ti, it)
                    except Exception as exc:
                        # 全帧管线是增强能力（依赖 turbojpeg/cv2）：失败降级跳过该通道，
                        # 层1 压缩域 + 层2 采样（PIL）仍完整工作，不拖垮整个 content 阶段
                        logger.warning("全帧管线跳过 topic=%s: %s", name, exc)
                        continue
                    finally:
                        self.config = _orig_cfg
                    # 找到层1的同名 CheckResult，合并事件 + detail
                    merged = False
                    for c in checks:
                        if c.name == f"content_image_{name}":
                            c.events = list(c.events) + list(new_check.events)
                            c.detail = dict(c.detail or {})
                            c.detail["fullframe"] = new_check.detail
                            # data_freeze 冲突：全帧 xxhash 为准，移除层1 size+head 指纹事件
                            if any(e.type == "data_freeze" for e in new_check.events):
                                c.events = [e for e in c.events
                                            if not (e.type == "data_freeze"
                                                    and not any(e2 is e for e2 in new_check.events))]
                            # severity 取两者较重
                            rank = {"pass": 0, "warning": 1, "error": 2}
                            c.severity = max([c.severity, new_check.severity],
                                             key=lambda s: rank.get(s, 0))
                            c.passed = c.severity == "pass"
                            merged = True
                            break
                    if not merged:
                        checks.append(new_check)
                    fullframe_done.add(name)

            # 层2（像素解码）依赖 Pillow：缺失时显式标注，避免逐通道静默"解码不可用"
            _pil_ok = True
            if image_ch and self.config.unpack and self.config.image_layer2:
                try:
                    import PIL  # noqa: F401
                except Exception:
                    _pil_ok = False
                    checks.append(CheckResult(
                        name="content_image_layer2",
                        passed=True, severity="pass",
                        message="Pillow 未安装，图像第二层（像素检测）整体跳过",
                        events=[], detail={"skipped": True, "reason": "pillow_not_installed"},
                    ))
            for name in image_ch:
                if not (self.config.unpack and self.config.image_layer2):
                    continue
                if name in fullframe_done:
                    continue
                if not _pil_ok:
                    continue
                ti = info.topics[name]
                positions = self.focus_positions(
                    ti.count, px_idx.get(name, []), image_anomaly.get(name, set()))
                if plan is not None:
                    px = plan[name]["pixels"]
                    frames_px = [px.get(p) for p in positions]
                    # E12: plan 只收集基础采样位——层1 异常焦点帧按需解码，
                    # 避免自适应采样对 bag/mcap 静默退化为固定 9 帧
                    missing = [p for p in positions if p not in px]
                    if missing:
                        extra_px = reader.image_pixels(name, missing) if missing else []
                        if extra_px:
                            for p, arr in zip(missing, extra_px):
                                frames_px[positions.index(p)] = arr
                else:
                    frames_px = reader.image_pixels(name, positions) if positions else []
                if frames_px and any(f is not None for f in frames_px):
                    _orig_cfg = self.config
                    self.config = self._cfg_for(name)
                    try:
                        checks.append(self.check_image_pixels(name, ti, frames_px, positions))
                    finally:
                        self.config = _orig_cfg

            # 事件富集 + 冻结交叉验证（需在 reader 关闭前，交叉验证要采样参考通道）
            self._enrich_events(info, checks)
            self._cross_validate_freeze(info, reader, checks)
            self._cross_validate_blackscreen(checks)
        finally:
            if created:
                reader.close()

        for c in checks:
            all_events.extend(c.events)
        # 指标级开关：事件按 code 过滤（indicators:{code:false}；含冻结前缀映射）
        if self.config.disabled_codes:
            from ...indicators.registry import EVENT_TYPE_TO_CODE
            _kept = []
            for e in all_events:
                _c = EVENT_TYPE_TO_CODE.get(getattr(e, "type", ""), "")
                if _c and _c in self.config.disabled_codes:
                    continue
                if getattr(e, "type", "") == "data_freeze":
                    _src = next((ck.name for ck in checks
                                 if getattr(e, "topic", "") and ck.name.endswith(getattr(e, "topic", ""))), "")
                    from ...indicators.registry import FREEZE_PREFIX_TO_CODE
                    for _pfx, _fc in FREEZE_PREFIX_TO_CODE.items():
                        if _src.startswith("content_" + _pfx[len("content_"):]) and _fc in self.config.disabled_codes:
                            _c = _fc
                            break
                if _c and _c in self.config.disabled_codes:
                    continue
                _kept.append(e)
            all_events = _kept

        overall_severity, summary = self._summarize(checks)
        return ContentResult(
            bag_path=bag_path, checks=checks, events=all_events,
            overall_severity=overall_severity, summary=summary,
        )

    def sample_positions(self, total: int) -> List[int]:
        """采样位置：头 / 中 / 尾"""
        positions = set()
        for i in range(min(self.config.sampling_head_count, total)):
            positions.add(i)
        for i in range(max(0, total - self.config.sampling_tail_count), total):
            positions.add(i)
        if total > self.config.sampling_head_count + self.config.sampling_tail_count:
            mid = total // 2
            half = self.config.sampling_mid_count // 2
            for i in range(max(0, mid - half),
                           min(total, mid + half + self.config.sampling_mid_count % 2)):
                positions.add(i)
        return sorted(positions)

    def focus_positions(self, total: int, base: List[int],
                         anomaly: set) -> List[int]:
        """第二层自适应采样 = 基础头/中/尾 + 层1异常区域的补充采样。

        异常补充按 `image_layer2_focus_max` 上限；超限时均匀抽取以保持分布。
        平时层1无异常时退化为固定抽样（不增加成本）。
        """
        extra = sorted(p for p in anomaly if 0 <= p < total and p not in base)
        cap = max(0, self.config.image_layer2_focus_max)
        if len(extra) > cap:
            step = len(extra) / max(1, cap)
            extra = [extra[int(i * step)] for i in range(cap)]
        return sorted(set(base) | set(extra))

    def _cross_validate_blackscreen(self, checks: List[CheckResult]) -> None:
        for c in checks:
            if not c.name.startswith("content_image_"):
                continue
            if c.name.startswith("content_image_pixels_"):
                continue
            if c.name == "content_image_layer2":
                continue
            topic = c.name[len("content_image_"):]
            if "depth" in topic.lower():
                continue
            ff = c.detail.get("fullframe") if isinstance(c.detail, dict) else None
            if not isinstance(ff, dict) or ff.get("skipped"):
                continue
            if any(e.type in ("pixel_black", "pixel_white") for e in c.events):
                continue
            n = sum(1 for e in c.events if e.type == "image_collapse")
            if not n:
                continue
            c.events = [e for e in c.events if e.type != "image_collapse"]
            if isinstance(c.detail, dict):
                c.detail["collapse_segments"] = []
                c.detail["collapse_corrected"] = n
            rank = {"pass": 0, "warning": 1, "error": 2}
            sev = max((e.severity for e in c.events),
                      key=lambda s: rank.get(s, 0), default="pass")
            c.severity = sev
            c.passed = sev == "pass"
            if sev == "pass":
                c.message = f"{topic}: 压缩域黑屏提名({n}段)经全帧像素复核为误报，已纠正"
            else:
                per_type: Dict[str, int] = {}
                for e in c.events:
                    per_type[e.type] = per_type.get(e.type, 0) + 1
                parts = ", ".join(f"{t}×{v}" for t, v in sorted(per_type.items()))
                c.message = (f"{topic}: 压缩域黑屏提名({n}段)经全帧像素复核为误报；"
                             f"其余事件: {parts}")

    def _summarize(self, checks: List[CheckResult]) -> Tuple[Literal["pass", "warning", "error"], Dict[str, int]]:
        err = sum(1 for c in checks if c.severity == "error")
        warn = sum(1 for c in checks if c.severity == "warning")
        ok = sum(1 for c in checks if c.severity == "pass")
        if err > 0:
            return "error", {"error_count": err, "warning_count": warn, "pass_count": ok}
        elif warn > 0:
            return "warning", {"error_count": err, "warning_count": warn, "pass_count": ok}
        return "pass", {"error_count": err, "warning_count": warn, "pass_count": ok}
