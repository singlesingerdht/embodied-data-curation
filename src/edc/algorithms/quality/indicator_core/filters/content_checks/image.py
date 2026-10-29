"""图像检测 mixin（第一层压缩域 + 第二层采样像素 + JPEG 结构校验）"""
import numpy as np
from typing import List, Dict, Optional, Any, Tuple, Iterator
from .model import ContentEvent, CheckResult

# 帧间编码的视频流（msg_type）："video/mp4"（lerobot videos 通道）与
# PyAV codec name（mp4 reader 的 s.codec_context.name：h264/hevc/...）。
# 这类流的压缩包尺寸∝帧间残差，不能作为内容熵代理（详见 _check_image_compressed）。
# 注意 mjpeg 除外：帧内编码，每包即完整 JPEG，尺寸启发式仍有效。
_VIDEO_MSG_TYPES = {
    "video/mp4", "video/mp4s", "h264", "h265", "hevc", "av1",
    "vp8", "vp9", "mpeg4", "mpeg2video", "mpeg1video", "theora",
}


def _is_video_codec(msg_type: str) -> bool:
    """通道是否为帧间编码视频流（尺寸启发式不适用）"""
    return msg_type.strip().lower() in _VIDEO_MSG_TYPES


class ImageChecksMixin:
    def _check_image_compressed(
        self, topic: str, ti, frames: Optional[Iterator[Tuple[int, int, bytes, bytes]]],
    ) -> CheckResult:
        """第一层压缩域检测

        只使用压缩帧的「长度 + 头尾字节」，不做像素解码：
        - 黑/白屏：压缩尺寸远低于中位数（熵骤减）
        - 模糊：压缩尺寸持续处于低位带（高频信息少）
        - 冻结：连续帧压缩字节指纹相同
        - 码流损坏：JPEG 缺失结束标记 / 空帧
        """
        events: List[ContentEvent] = []
        is_compressed = bool(ti.image_compressed)
        if frames is None:
            return CheckResult(
                name=f"content_image_{topic}", passed=True, severity="pass",
                message=f"{topic}: 无法读取图像帧，跳过", events=[], detail={"skipped": True},
            )

        sizes: List[int] = []
        frame_ids: List[int] = []
        jpeg_infos: List[dict] = []
        prev_fp = None
        freeze_count = 0
        freeze_start = None
        freeze_segments: List[dict] = []

        for idx, size, head, tail in frames:
            if idx >= ti.count:
                break  # 时间轴短于数据行时，只扫描声明范围内的帧（防事件 index 越界）
            if size == 0:
                # 空包（如 mp4 流末尾 EOS / 缺失帧），不计入尺寸统计
                continue
            sizes.append(size)
            frame_ids.append(idx)

            # JPEG 分辨率/编码（解析 SOF 段，不解码像素）
            if head[:2] == b"\xff\xd8":
                jinfo = self._jpeg_info(head)
                if jinfo is not None:
                    jinfo["idx"] = idx
                    jpeg_infos.append(jinfo)

            # 冻结：指纹 = (长度, 前256字节)
            fp = (size, head)
            if fp == prev_fp and size > 0:
                freeze_count += 1
                if freeze_start is None:
                    freeze_start = idx
            else:
                if freeze_count >= self.config.image_freeze_low_frames:
                    freeze_segments.append({"start": freeze_start, "end": idx - 1, "count": freeze_count})
                    sev = "error" if freeze_count >= self.config.image_freeze_high_frames else "warning"
                    events.append(ContentEvent(
                        type="data_freeze", severity=sev, topic=topic, index=freeze_start,
                        detail={"count": freeze_count, "range": [freeze_start, idx - 1]}))
                freeze_count = 0
                freeze_start = None
            prev_fp = fp

            # JPEG 完整性（仅对以 JPEG magic 开头的帧，避免 depth/自定义格式误判）
            if head[:2] == b"\xff\xd8":
                ok, reason = self._jpeg_marker_check(head)
                if not ok:
                    events.append(ContentEvent(
                        type="image_corrupt", severity="error", topic=topic, index=idx,
                        detail={"reason": f"jpeg_{reason}", "size": size}))
                elif tail[-2:] != b"\xff\xd9":
                    events.append(ContentEvent(
                        type="image_corrupt", severity="error", topic=topic, index=idx,
                        detail={"reason": "jpeg_missing_eoi", "size": size}))
            elif 0 < size < 2:
                events.append(ContentEvent(
                    type="image_corrupt", severity="error", topic=topic, index=idx,
                    detail={"reason": "empty_frame", "size": size}))

        # 末尾冻结段
        if freeze_count >= self.config.image_freeze_low_frames:
            end = freeze_start + freeze_count
            freeze_segments.append({"start": freeze_start, "end": end - 1, "count": freeze_count})
            sev = "error" if freeze_count >= self.config.image_freeze_high_frames else "warning"
            events.append(ContentEvent(
                type="data_freeze", severity=sev, topic=topic, index=freeze_start,
                detail={"count": freeze_count, "range": [freeze_start, end - 1]}))

        if len(sizes) < 2:
            return CheckResult(
                name=f"content_image_{topic}", passed=True, severity="pass",
                message=f"{topic}: 帧数不足({len(sizes)})，跳过",
                events=events, detail={"skipped": True, "reason": "insufficient_frames"},
            )

        median = float(np.median(sizes)) if sizes else 0.0
        detail: Dict[str, Any] = {
            "total_count": len(sizes),
            "median_size": int(median),
            "is_compressed": is_compressed,
        }

        # ---- JPEG 分辨率 / 编码突变（Tier 1）----
        res_change: List[dict] = []
        enc_change: List[dict] = []
        if jpeg_infos:
            base = jpeg_infos[0]
            cur_res = (base["width"], base["height"])
            cur_enc = (base["ncomp"], base["progressive"], base["precision"])
            for j in jpeg_infos[1:]:
                res = (j["width"], j["height"])
                enc = (j["ncomp"], j["progressive"], j["precision"])
                if res != cur_res:
                    res_change.append({"index": j["idx"], "from": cur_res, "to": res})
                    cur_res = res
                if enc != cur_enc:
                    enc_change.append({"index": j["idx"], "from": {"ncomp": cur_enc[0], "progressive": cur_enc[1]},
                                       "to": {"ncomp": j["ncomp"], "progressive": j["progressive"]}})
                    cur_enc = enc
            dims = {f"{j['width']}x{j['height']}" for j in jpeg_infos}
            detail["resolution_variants"] = sorted(dims)
        # 设计表 C6：分辨率突变 1-2 次（轻微，可统一规格）→ warning；≥3 次（频繁）→ error
        res_sev = "error" if len(res_change) >= 3 else "warning"
        for rc in res_change:
            events.append(ContentEvent(
                type="image_resolution_change", severity=res_sev, topic=topic, index=rc["index"],
                detail={"from": list(rc["from"]), "to": list(rc["to"])}))
        for ec in enc_change:
            events.append(ContentEvent(
                type="image_encoding_change", severity="warning", topic=topic, index=ec["index"],
                detail={"from": ec["from"], "to": ec["to"]}))
        detail["resolution_changes"] = res_change
        detail["encoding_changes"] = enc_change

        collapse_segments: List[dict] = []
        blur_segments: List[dict] = []

        # 视频编码流守卫：H.264/HEVC 等帧间编码的包尺寸∝帧间运动残差而非内容熵
        # （静止场景 P 帧仅数百字节属正常），尺寸类启发式（黑/白屏骤减、模糊低位带）
        # 对其无效——跳过，内容质量交给全帧像素管线（image_bytes 逐帧解码）。
        # 冻结指纹（连续同包）与码流损坏检查不受影响，仍保留。
        is_video_codec = _is_video_codec(getattr(ti, "msg_type", "") or "")
        if is_video_codec:
            detail["is_video_codec"] = True
            detail["size_heuristics_skipped"] = "video_codec_stream"

        if is_compressed and median > 0 and not is_video_codec:
            collapse_thr = max(self.config.image_abs_size_floor_bytes,
                               median * self.config.image_collapse_ratio)
            blur_low = median * self.config.image_blur_low_ratio
            blur_high = median * self.config.image_blur_high_ratio

            # 黑/白屏：尺寸 <= 阈值（分段合并，要求连续 >= min_run）
            run = 0
            start = None
            for i, s in enumerate(sizes):
                if s <= collapse_thr:
                    run += 1
                    if start is None:
                        start = i
                else:
                    if run >= self.config.image_collapse_min_run:
                        collapse_segments.append({"start": frame_ids[start], "end": frame_ids[i - 1],
                                                  "count": run})
                    run = 0
                    start = None
            if run >= self.config.image_collapse_min_run:
                collapse_segments.append({"start": frame_ids[start], "end": frame_ids[-1],
                                          "count": run})

            for seg in collapse_segments:
                events.append(ContentEvent(
                    type="image_collapse", severity="warning", topic=topic, index=seg["start"],
                    detail={"reason": "black_or_white_screen",
                            "range": [seg["start"], seg["end"]], "count": seg["count"],
                            "threshold": int(collapse_thr), "median": int(median)}))

            # 模糊：尺寸处于低位带且连续
            run = 0
            start = None
            for i, s in enumerate(sizes):
                if blur_low <= s <= blur_high:
                    run += 1
                    if start is None:
                        start = i
                else:
                    if run >= self.config.image_blur_min_run:
                        blur_segments.append({"start": frame_ids[start], "end": frame_ids[i - 1],
                                              "count": run})
                    run = 0
                    start = None
            if run >= self.config.image_blur_min_run:
                blur_segments.append({"start": frame_ids[start], "end": frame_ids[-1],
                                      "count": run})

            for seg in blur_segments:
                events.append(ContentEvent(
                    type="blur_suspect", severity="warning", topic=topic, index=seg["start"],
                    detail={"range": [seg["start"], seg["end"]], "count": seg["count"],
                            "median": int(median)}))

        detail["collapse_segments"] = collapse_segments
        detail["blur_segments"] = blur_segments
        detail["freeze_segments"] = freeze_segments

        has_corrupt = any(e.type == "image_corrupt" for e in events)
        has_res_change = any(e.type == "image_resolution_change" for e in events)
        has_collapse = any(e.type == "image_collapse" for e in events)
        has_freeze = any(e.type == "data_freeze" for e in events)
        has_blur = any(e.type == "blur_suspect" for e in events)

        if has_corrupt:
            severity = "error"; passed = False
            msg = (f"{topic}: 检测到 {sum(1 for e in events if e.type=='image_corrupt')}"
                   f" 个损坏帧(JPEG 缺结束标记/空帧)")
        elif has_res_change:
            severity = res_sev; passed = res_sev == "pass"
            msg = f"{topic}: 检测到 {len(res_change)} 处分辨率突变({severity})"
        elif has_collapse:
            severity = "warning"; passed = False
            msg = f"{topic}: 检测到 {len(collapse_segments)} 段黑/白屏(压缩尺寸骤减)"
        elif has_freeze:
            severity = "warning"; passed = False
            msg = f"{topic}: 图像冻结 {len(freeze_segments)} 段"
        elif has_blur:
            severity = "warning"; passed = False
            msg = f"{topic}: 检测到 {len(blur_segments)} 段模糊(压缩尺寸持续偏低)"
        else:
            severity = "pass"; passed = True
            msg = f"{topic}: 压缩域正常 ({len(sizes)}帧, 中位 {int(median)}B)"

        return CheckResult(
            name=f"content_image_{topic}", passed=passed, severity=severity,
            message=msg, events=events, detail=detail,
        )

    @staticmethod
    def _jpeg_info(head: bytes) -> Optional[dict]:
        """从 JPEG 帧头解析分辨率/编码（SOF 段），不解码像素

        返回 {width, height, precision, ncomp, progressive}；非 JPEG / 头内
        找不到 SOF 返回 None。
        """
        if not head.startswith(b"\xff\xd8"):
            return None
        n = len(head)
        i = 2
        while i < n - 1:
            if head[i] != 0xFF:
                i += 1
                continue
            while i < n and head[i] == 0xFF:
                i += 1
            if i >= n:
                break
            m = head[i]
            i += 1
            if m == 0xD8 or m == 0x01 or 0xD0 <= m <= 0xD7:
                continue  # 无长度段的标记
            if m == 0xDA:
                break      # SOS：其后为扫描数据
            if i + 2 > n:
                break
            seg_len = (head[i] << 8) | head[i + 1]
            if seg_len < 2:
                break
            if (0xC0 <= m <= 0xC3) or (0xC5 <= m <= 0xC7) \
                    or (0xC9 <= m <= 0xCB) or (0xCD <= m <= 0xCF):
                # SOF
                if i + 2 + 6 > n:
                    break
                return {
                    "precision": head[i + 2],
                    "height": (head[i + 3] << 8) | head[i + 4],
                    "width": (head[i + 5] << 8) | head[i + 6],
                    "ncomp": head[i + 7],
                    "progressive": m in (0xC2, 0xC6, 0xCA, 0xCE),
                }
            i += seg_len - 2
        return None

    @staticmethod
    def _jpeg_marker_check(head: bytes) -> Tuple[bool, str]:
        """JPEG marker 流结构校验（不解码像素）

        校验 SOI → 段序列（长度合法/非法标记/保留段）→ SOF 前必须有帧头 → SOS。
        返回 (ok, reason)；reason 为 'ok' 或具体损坏原因。
        """
        if not head.startswith(b"\xff\xd8"):
            return False, "no_soi"
        n = len(head)
        i = 2
        seen_sof = False
        while i < n - 1:
            if head[i] != 0xFF:
                break  # SOS 后的扫描数据或 0xFF00 填充
            while i < n and head[i] == 0xFF:
                i += 1
            if i >= n:
                return True, "ok"  # 头不足，交由 EOI 校验
            m = head[i]
            i += 1
            if m == 0x00:
                continue            # 0xFF00 字节填充
            if m == 0xD8 or m == 0x01 or 0xD0 <= m <= 0xD7:
                continue            # SOI / TEM / RST（无长度段）
            if m == 0xDA:
                # SOS：其后为扫描数据；若无帧头则损坏
                return (True, "ok") if seen_sof else (False, "sos_without_sof")
            if m == 0xD9:
                return False, "early_eoi"
            if 0x02 <= m <= 0xBF:
                return False, f"invalid_marker_{m:02x}"
            if i + 2 > n:
                return True, "ok"   # 头不足，交由 EOI 校验
            seg_len = (head[i] << 8) | head[i + 1]
            if seg_len < 2:
                return False, "bad_segment_length"
            if 0xC0 <= m <= 0xCF:
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    seen_sof = True
            i += seg_len - 2
        return True, "ok"

    def check_image_pixels(
        self, topic: str, ti, frames: List[Any], positions: List[int],
    ) -> CheckResult:
        """第二层像素检测：对采样帧内存解码，检测遮挡/曝光/色彩/模糊/噪声

        只解码头/中/尾少量帧（config.image_pixel_sample_count），算完即弃，
        不产生任何文件。
        """
        count = ti.count
        if count < 3:
            return CheckResult(
                name=f"content_image_pixels_{topic}", passed=True, severity="pass",
                message=f"{topic}: 帧数不足，跳过", events=[], detail={"skipped": True},
            )
        if not frames:
            return CheckResult(
                name=f"content_image_pixels_{topic}", passed=True, severity="pass",
                message=f"{topic}: 第二层解码不可用，跳过",
                events=[], detail={"skipped": True, "reason": "decode_unsupported"},
            )

        events: List[ContentEvent] = []
        sharp_list: List[Tuple[int, float]] = []
        decoded = 0
        for pos, arr in zip(positions, frames):
            if arr is None:
                continue
            arr = self._downsample(arr)
            decoded += 1
            sharp_list.append((pos, self._voL(arr.mean(axis=2))))
            for e in self._image_pixel_events(arr, pos, topic=topic):
                events.append(e)

        if decoded == 0:
            return CheckResult(
                name=f"content_image_pixels_{topic}", passed=True, severity="pass",
                message=f"{topic}: 第二层解码不可用（深度/自定义编码），跳过",
                events=[], detail={"skipped": True, "reason": "decode_failed"},
            )

        # 模糊：相对下降（失焦事件）+ 绝对过低（相机持续模糊）
        median_sh = float(np.median([s for _, s in sharp_list]))
        for pos, sh in sharp_list:
            if len(sharp_list) >= 2 and median_sh > 0 and sh < self.config.image_blur_ratio * median_sh:
                events.append(ContentEvent(
                    type="pixel_blur", severity="warning", topic=topic, index=pos,
                    detail={"sharpness": round(sh, 1), "median": round(median_sh, 1),
                            "reason": "relative_drop"}))
        if median_sh < self.config.image_sharpness_floor:
            events.append(ContentEvent(
                type="pixel_low_sharpness", severity="warning", topic=topic, index=-1,
                detail={"sharpness_median": round(median_sh, 1),
                        "floor": self.config.image_sharpness_floor,
                        "reason": "camera_level_soft"}))

        per_type: Dict[str, int] = {}
        for e in events:
            per_type[e.type] = per_type.get(e.type, 0) + 1

        sharpness_level = "high" if median_sh > 1000 else "medium" if median_sh > 200 else "low"
        detail = {
            "sampled": decoded,
            "sharpness_voL_median": round(median_sh, 1),
            "sharpness_level": sharpness_level,
            "issues": dict(per_type),
        }

        if not events:
            return CheckResult(
                name=f"content_image_pixels_{topic}", passed=True, severity="pass",
                message=f"{topic}: 像素检测正常 ({decoded}帧采样, 清晰度{sharpness_level})",
                events=[], detail=detail,
            )

        label = {"pixel_black": "黑屏", "pixel_white": "白屏", "pixel_overexpo": "过曝",
                 "pixel_underexpo": "欠曝", "pixel_blur": "模糊", "pixel_occlusion": "遮挡",
                 "pixel_low_sharpness": "持续低清晰度", "pixel_garbled": "花屏/雪花",
                 "pixel_color_anomaly": "严重偏色"}
        parts = [f"{label.get(t, t)}×{c}" for t, c in sorted(per_type.items())]

        # C5/C9 占比三档：坏帧（黑/白/遮挡/花屏/过曝/欠曝/偏色）≤5% / 5-30% / ≥30%；
        # 模糊帧 ≤10% / 10-30% / ≥30%（设计表处置档，采样帧近似）
        bad_types = {"pixel_black", "pixel_white", "pixel_overexpo", "pixel_underexpo",
                     "pixel_occlusion", "pixel_garbled", "pixel_color_anomaly"}
        blur_types = {"pixel_blur", "pixel_low_sharpness"}
        bad_frames = len({e.index for e in events if e.type in bad_types and e.index >= 0})
        blur_frames = len({e.index for e in events if e.type in blur_types and e.index >= 0})
        bad_ratio = bad_frames / max(1, decoded)
        blur_ratio = blur_frames / max(1, decoded)
        sev_bad = ("error" if bad_ratio >= self.config.bad_frame_high
                   else "warning" if bad_ratio > self.config.bad_frame_low else "pass")
        sev_blur = ("error" if blur_ratio >= self.config.blur_high
                    else "warning" if blur_ratio > self.config.blur_low else "pass")
        severity = max(sev_bad, sev_blur, key={"pass": 0, "warning": 1, "error": 2}.get)
        # 相机级持续低清晰度（pixel_low_sharpness，index=-1 哨兵）不参与采样帧占比，
        # 但其存在本身即 warning 事件（相机整体模糊）→ check 至少 warning，避免事件/check 矛盾
        if severity == "pass" and any(e.type == "pixel_low_sharpness" for e in events):
            severity = "warning"
        detail["bad_ratio"] = round(bad_ratio, 3)
        detail["blur_ratio"] = round(blur_ratio, 3)
        _px_err = getattr(self, "pixel_errors", 0)
        if _px_err:
            detail = dict(detail or {})
            detail["pixel_errors"] = _px_err
            detail["last_pixel_error"] = getattr(self, "last_pixel_error", "")
            self.pixel_errors = 0
        return CheckResult(
            name=f"content_image_pixels_{topic}", passed=severity == "pass", severity=severity,
            message=f"{topic}: 第二层像素检测 - " + ", ".join(parts),
            events=events, detail=detail,
        )

    @staticmethod
    def _downsample(arr: np.ndarray) -> np.ndarray:
        """大图降采样（聚合指标在子采样下等价），压到 ~480 宽以控制计算量"""
        h, w = arr.shape[:2]
        step = max(1, max(h, w) // 480)
        if step > 1:
            return arr[::step, ::step]
        return arr

    @staticmethod
    def _voL(gray: np.ndarray) -> float:
        """方差拉普拉斯（Variance of Laplacian）清晰度指标"""
        from scipy.ndimage import convolve
        k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=float)
        lap = convolve(np.asarray(gray, dtype=float), k, mode="reflect")
        return float(lap.var())

    def _image_pixel_events(self, arr: np.ndarray, idx: int, topic: str = "") -> List[ContentEvent]:
        """对单帧解码像素计算指标并产出事件（黑/白/曝光/遮挡）"""
        events: List[ContentEvent] = []
        try:
            gray = arr.mean(axis=2)
            mean = float(gray.mean())
            std = float(gray.std())
            hh, ww = gray.shape
            if hh < 4 or ww < 4:
                return events

            # 分块遮挡检测（向量化：一次 reshape 求 4x4 块均值）
            bh, bw = hh // 4, ww // 4
            if bh > 0 and bw > 0:
                blocks = gray[:bh * 4, :bw * 4].reshape(bh, 4, bw, 4).mean(axis=(1, 3))
                cells = blocks.ravel()
                black_frac = float((cells < self.config.pixel_occlusion_black_cell).mean())
                dark_frac = float((cells < self.config.pixel_occlusion_dark_cell).mean())
            else:
                cells = np.asarray([gray.mean()])
                black_frac = float(cells[0] < self.config.pixel_occlusion_black_cell)
                dark_frac = float(cells[0] < self.config.pixel_occlusion_dark_cell)

            dark_ratio = float((gray < self.config.pixel_black_saturate_level).mean())
            if mean < self.config.pixel_black_mean and dark_ratio > self.config.pixel_black_saturate_ratio:
                events.append(ContentEvent(
                    type="pixel_black", severity="warning", topic=topic, index=idx,
                    detail={"mean": round(mean, 1), "dark_ratio": round(dark_ratio, 3)}))
            elif mean > self.config.pixel_white_mean:
                light_ratio = float((gray > self.config.pixel_white_saturate_level).mean())
                if light_ratio > self.config.pixel_white_saturate_ratio:
                    events.append(ContentEvent(
                        type="pixel_white", severity="warning", topic=topic, index=idx,
                        detail={"mean": round(mean, 1), "light_ratio": round(light_ratio, 3)}))
            elif mean > self.config.pixel_overexpo_mean and std > self.config.pixel_overexpo_std:
                events.append(ContentEvent(
                    type="pixel_overexpo", severity="warning", topic=topic, index=idx,
                    detail={"mean": round(mean, 1), "std": round(std, 1)}))
            elif self.config.pixel_black_mean < mean < self.config.pixel_underexpo_mean and std > self.config.pixel_underexpo_std:
                events.append(ContentEvent(
                    type="pixel_underexpo", severity="warning", topic=topic, index=idx,
                    detail={"mean": round(mean, 1), "std": round(std, 1)}))

            if self.config.pixel_occlusion_frac_low <= dark_frac <= self.config.pixel_occlusion_frac_high and mean < self.config.pixel_occlusion_mean:
                events.append(ContentEvent(
                    type="pixel_occlusion", severity="warning", topic=topic, index=idx,
                    detail={"dark_cell_frac": round(dark_frac, 3), "mean": round(mean, 1)}))
            elif self.config.pixel_occlusion_frac_low <= black_frac <= self.config.pixel_occlusion_frac_high and mean < self.config.pixel_occlusion_mean:
                events.append(ContentEvent(
                    type="pixel_occlusion", severity="warning", topic=topic, index=idx,
                    detail={"black_cell_frac": round(black_frac, 3), "mean": round(mean, 1)}))

            gx = np.abs(np.diff(gray, axis=1))
            gy = np.abs(np.diff(gray, axis=0))
            grad = (gx[1:, :] + gy[:, 1:]) / 2.0
            strong = float((grad > self.config.pixel_garbled_grad_threshold).mean())
            grad_mean = float(grad.mean())
            if strong > self.config.pixel_garbled_strong_frac and grad_mean > self.config.pixel_garbled_grad_mean:
                events.append(ContentEvent(
                    type="pixel_garbled", severity="warning", topic=topic, index=idx,
                    detail={"strong_grad_frac": round(strong, 3),
                            "grad_mean": round(grad_mean, 1)}))

            ch = arr.mean(axis=(0, 1))
            ch_range = float(ch.max() - ch.min())
            if ch_range > self.config.pixel_color_anomaly_range:
                events.append(ContentEvent(
                    type="pixel_color_anomaly", severity="warning", topic=topic, index=idx,
                    detail={"channel_means": [round(float(c), 1) for c in ch],
                            "range": round(ch_range, 1)}))
        except Exception as e:
            self.pixel_errors = getattr(self, "pixel_errors", 0) + 1
            self.last_pixel_error = f"{type(e).__name__}: {e}"
        return events


class FrameQualityAnalyzer:
    """全帧极速图像质检状态机。

    逐帧 feed(idx, ts, payload) → 内部 1/4 灰度解码 + 5 检测 + 状态机。
    payload: bytes(JPEG/PNG/zlib 编码字节) 或 np.ndarray(原始 RGB/gray 像素，免解码)。

    设计：
    - feed() 只做单帧可定的判定 + 记录，状态机在 report() 一次性解析。
    - 黑/白/遮挡/花屏/模糊 单帧可定；冻结用 xxhash 全帧哈希 run-length；模糊相对降焦需
      全局中位，故也在 report() 二次判定。
    - 所有判定基于全帧 1/4 灰度 img，无 Python 像素级循环。
    """

    _MIN_DUR_SEC = 1.0
    _ERR_DUR_SEC = 5.0

    def __init__(self, config):
        self.cfg = config
        self._tj = None
        self._total = 0
        self._undecodable = 0
        self._frames = []
        self.events = []
        self.is_depth = False
        self._flat_history = []
        self._prev_img = None

    def _turbojpeg(self):
        if self._tj is None:
            try:
                from turbojpeg import TurboJPEG
                self._tj = TurboJPEG()
            except Exception:
                self._tj = False
        return self._tj if self._tj is not False else None

    def _decode_qgray(self, payload) -> Optional[np.ndarray]:
        import cv2
        if isinstance(payload, np.ndarray) and payload.size:
            arr = payload
            if arr.ndim == 3:
                if arr.shape[2] == 1:
                    arr = arr[..., 0]
                else:
                    arr = arr.astype(np.float32).mean(axis=2).astype(np.uint8)
            return arr[::4, ::4] if arr.ndim == 2 else None
        if not payload:
            return None
        if payload[:2] == b"\xff\xd8":
            tj = self._turbojpeg()
            if tj is not None:
                try:
                    from turbojpeg import TJPF_GRAY
                    g = tj.decode(payload, scaling_factor=(1, 4), pixel_format=TJPF_GRAY)
                    return g[..., 0] if g.ndim == 3 else g
                except Exception:
                    pass
            try:
                arr = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_GRAYSCALE)
                return None if arr is None else arr[::4, ::4]
            except Exception:
                return None
        try:
            arr = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_GRAYSCALE)
            return None if arr is None else arr[::4, ::4]
        except Exception:
            return None

    def _compute_flat_ratio(self, img: np.ndarray) -> Tuple[float, float]:
        import cv2
        h, w = img.shape
        gs = max(4, min(h, w) // 12)
        H, W = h // gs, w // gs
        if H < 2 or W < 2:
            return 1.0, 1.0
        sub = img[:H * gs, :W * gs].astype(np.float32)
        s1, s2 = cv2.integral2(sub)
        n = float(gs * gs)
        S = (s1[gs::gs, gs::gs] - s1[:-gs:gs, gs::gs]
             - s1[gs::gs, :-gs:gs] + s1[:-gs:gs, :-gs:gs])
        S2 = (s2[gs::gs, gs::gs] - s2[:-gs:gs, gs::gs]
              - s2[gs::gs, :-gs:gs] + s2[:-gs:gs, :-gs:gs])
        mb = S / n
        var = np.clip(S2 / n - mb * mb, 0.0, None)
        sigma = np.sqrt(var)
        flat = (sigma < 5.0).astype(np.uint8)
        flat_ratio = float(flat.mean())
        n_cc, _l, stats, _c = cv2.connectedComponentsWithStats(flat, connectivity=8)
        largest = float(stats[1:, 4].max()) / float(flat.size) if n_cc > 1 else 0.0
        return flat_ratio, largest

    def _check_occlusion(self, flat_ratio: float, largest_flat: float,
                         mean_val: float,
                         prev_img: Optional[np.ndarray] = None,
                         img: Optional[np.ndarray] = None) -> bool:
        if not (10.0 <= mean_val <= 245.0):
            return False
        if len(self._flat_history) >= 5:
            prev_flat_med = float(np.median([f[0] for f in self._flat_history[-5:]]))
            if prev_flat_med < 0.3:
                if flat_ratio > prev_flat_med + 0.10 and largest_flat > 0.15:
                    return True
            else:
                if (flat_ratio > 0.65 and largest_flat > 0.65
                        and flat_ratio > prev_flat_med + 0.15):
                    return True
        if len(self._flat_history) >= 10:
            recent = [f[0] for f in self._flat_history[-10:]]
            if float(np.median(recent)) > 0.9 and prev_img is not None and img is not None:
                mad = float(np.abs(img.astype(np.int16) - prev_img.astype(np.int16)).mean())
                if mad < 2.0:
                    return True
        return False

    def _check_garbled(self, img: np.ndarray) -> Tuple[bool, float]:
        a = img.astype(np.int16)
        diff_x = np.abs(np.diff(a, axis=1))
        internal_x = diff_x[:, 0::2]
        boundary_x = diff_x[:, 1::2]
        Gb_x = float(boundary_x.mean()) if boundary_x.size else 0.0
        Gi_x = float(internal_x.mean()) if internal_x.size else 0.0
        diff_y = np.abs(np.diff(a, axis=0))
        internal_y = diff_y[0::2, :]
        boundary_y = diff_y[1::2, :]
        Gb_y = float(boundary_y.mean()) if boundary_y.size else 0.0
        Gi_y = float(internal_y.mean()) if internal_y.size else 0.0
        bbd = max(Gb_x / (Gi_x + 1e-5), Gb_y / (Gi_y + 1e-5))
        grad_mean = max(float(diff_x.mean()), float(diff_y.mean()))
        is_bbd = bbd > 1.8 and grad_mean > 15.0
        is_grad = self._check_garbled_gradient(img)
        return (is_bbd or is_grad), bbd

    def _check_garbled_gradient(self, img: np.ndarray) -> bool:
        h, w = img.shape
        if h < 8 or w < 8:
            return False
        g = img.astype(np.float32)
        gx = np.abs(np.diff(g, axis=1))
        gy = np.abs(np.diff(g, axis=0))
        grad = (gx[1:, :] + gy[:, 1:]) / 2.0
        threshold = self.cfg.pixel_garbled_grad_threshold
        strong_frac = float((grad > threshold).mean())
        grad_mean = float(grad.mean())
        return (strong_frac > self.cfg.pixel_garbled_strong_frac
                and grad_mean > self.cfg.pixel_garbled_grad_mean)

    def _feed_hash(self, payload) -> Optional[str]:
        import xxhash
        if isinstance(payload, np.ndarray) and payload.size:
            return xxhash.xxh64(np.ascontiguousarray(payload)).hexdigest()
        if payload:
            return xxhash.xxh64(payload).hexdigest()
        return None

    def feed(self, idx: int, ts: float, payload: Any) -> None:
        import cv2
        self._total += 1
        img = self._decode_qgray(payload)
        if img is None or img.size == 0 or img.shape[0] < 4 or img.shape[1] < 4:
            self._undecodable += 1
            self._frames.append((idx, ts, False, False, False, False, 0.0, None,
                                 False, False))
            self._prev_img = None
            return
        mean_val = float(img.mean())
        if self.is_depth:
            is_black = False
            is_white = False
        else:
            dark_ratio = float((img < self.cfg.pixel_black_saturate_level).mean())
            is_black = mean_val < self.cfg.pixel_black_mean \
                or (mean_val < 8.0 and dark_ratio > 0.8)
            is_white = mean_val > self.cfg.pixel_white_mean
        # 过曝/欠曝（与 layer2 _image_pixel_events 同判据；黑白优先互斥）
        std_val = float(img.std())
        is_overexpo = (not is_black and not is_white) \
            and mean_val > self.cfg.pixel_overexpo_mean \
            and std_val > self.cfg.pixel_overexpo_std
        is_underexpo = (not is_black and not is_white) \
            and self.cfg.pixel_black_mean < mean_val < self.cfg.pixel_underexpo_mean \
            and std_val > self.cfg.pixel_underexpo_std
        flat_ratio, largest_flat = self._compute_flat_ratio(img)
        is_occl = self._check_occlusion(flat_ratio, largest_flat, mean_val,
                                        prev_img=self._prev_img, img=img)
        self._flat_history.append((flat_ratio, largest_flat))
        is_garbl, _bbd = self._check_garbled(img)
        blur_score = float(cv2.Laplacian(img, cv2.CV_64F).var())
        fhash = self._feed_hash(payload)
        self._frames.append((idx, ts, is_black, is_white, is_occl, is_garbl,
                             blur_score, fhash, is_overexpo, is_underexpo))
        self._prev_img = img

    def _emit_run(self, topic: str, etype: str, run_idx: List[int],
                  run_ts: List[float], detail: Dict[str, Any]) -> None:
        if not run_idx:
            return
        dur = (run_ts[-1] - run_ts[0]) if len(run_ts) > 1 else 0.0
        if etype == "data_freeze":
            n = len(run_idx)
            sev = "error" if n >= self.cfg.image_freeze_cut_run else "warning"
        else:
            sev = "error" if dur >= self._ERR_DUR_SEC else "warning"
        d = dict(detail)
        d["range"] = [run_idx[0], run_idx[-1]]
        d["duration_sec"] = round(dur, 3)
        self.events.append(ContentEvent(
            type=etype, severity=sev, topic=topic, index=run_idx[0],
            time=run_ts[0], detail=d))

    def report(self, topic: str, ti) -> CheckResult:
        import numpy as _np
        cfg = self.cfg
        total = max(1, self._total)
        frames = self._frames

        if self._undecodable / total > 0.5:
            return CheckResult(
                name=f"content_image_{topic}", passed=True, severity="pass",
                message=f"{topic}: 全帧解码不可用（深度/自定义编码 {self._undecodable}/{self._total} 帧），跳过",
                events=[], detail={"skipped": True, "reason": "decode_unsupported",
                                   "total_count": self._total,
                                   "undecodable": self._undecodable,
                                   "mode": "fullframe"})

        frozen = [False] * len(frames)
        min_run = cfg.image_freeze_min_run
        i = 0
        while i < len(frames):
            fh = frames[i][7]
            if fh is None:
                i += 1
                continue
            j = i + 1
            while j < len(frames) and frames[j][7] == fh:
                j += 1
            if j - i >= min_run:
                for k in range(i, j):
                    frozen[k] = True
            i = j

        scores = [f[6] for f in frames]
        median_sh = float(_np.median(scores)) if scores else 0.0
        if self.is_depth or median_sh < cfg.image_sharpness_floor:
            is_blur_frame = [False] * len(frames)
        else:
            is_blur_frame = [
                (s > 0 and s < cfg.image_blur_ratio * median_sh)
                for s in scores
            ]

        type_flags = {
            "pixel_black": [f[2] for f in frames],
            "pixel_white": [f[3] for f in frames],
            "pixel_occlusion": [f[4] for f in frames],
            "pixel_garbled": [f[5] for f in frames],
            "pixel_blur": is_blur_frame,
            "data_freeze": frozen,
            "pixel_overexpo": [f[8] for f in frames],
            "pixel_underexpo": [f[9] for f in frames],
        }
        for etype, flags in type_flags.items():
            r_idx: List[int] = []
            r_ts: List[float] = []
            for j, (idx_, ts_, _fh) in enumerate(zip(
                    [f[0] for f in frames], [f[1] for f in frames], frames)):
                if flags[j]:
                    r_idx.append(idx_)
                    r_ts.append(ts_)
                else:
                    if etype == "data_freeze" and len(r_idx) >= 1:
                        self._emit_run(topic, etype, r_idx, r_ts,
                                       {"count": len(r_idx), "motion": None})
                    else:
                        self._emit_run(topic, etype, r_idx, r_ts, {})
                    r_idx, r_ts = [], []
            if etype == "data_freeze" and len(r_idx) >= 1:
                self._emit_run(topic, etype, r_idx, r_ts,
                               {"count": len(r_idx), "motion": None})
            else:
                self._emit_run(topic, etype, r_idx, r_ts, {})

        # C5 语义坏帧占比：模糊属 C9、冻结属 C10，均不占坏帧占比（对齐层2 bad_types 口径）
        bad_types = ("pixel_black", "pixel_white", "pixel_occlusion",
                     "pixel_garbled")
        bad_count = sum(1 for j in range(len(frames))
                        if any(type_flags[t][j] for t in bad_types))
        blur_count = sum(is_blur_frame)
        bad_ratio = bad_count / total
        blur_ratio = blur_count / total

        rank = {"pass": 0, "warning": 1, "error": 2}
        sev_bad = ("error" if bad_ratio >= cfg.bad_frame_high
                   else "warning" if bad_ratio > cfg.bad_frame_low else "pass")
        sev_blur = ("error" if blur_ratio >= cfg.blur_high
                    else "warning" if blur_ratio > cfg.blur_low else "pass")
        sev_ev = max((e.severity for e in self.events), key=lambda s: rank.get(s, 0)) \
            if self.events else "pass"
        severity = max([sev_bad, sev_blur, sev_ev], key=lambda s: rank.get(s, 0))

        sharp_level = ("high" if median_sh > 1000
                       else "medium" if median_sh > 200 else "low")
        per_type: Dict[str, int] = {}
        for e in self.events:
            per_type[e.type] = per_type.get(e.type, 0) + 1

        detail = {
            "mode": "fullframe",
            "total_count": self._total,
            "frame_count": self._total,
            "undecodable": self._undecodable,
            "bad_ratio": round(bad_ratio, 4),
            "blur_ratio": round(blur_ratio, 4),
            "sharpness_voL_median": round(median_sh, 1),
            "sharpness_level": sharp_level,
            "issues": per_type,
        }
        passed = severity == "pass"
        if severity == "error":
            msg = f"{topic}: 全帧检测异常 ({len(self.events)}事件, 坏帧{bad_count}/{self._total}={bad_ratio:.1%})"
        elif severity == "warning":
            msg = f"{topic}: 全帧检测告警 ({len(self.events)}事件, 坏帧{bad_count}/{self._total}={bad_ratio:.1%})"
        else:
            msg = f"{topic}: 全帧检测正常 ({self._total}帧, 清晰度{sharp_level}, 坏帧{bad_count})"

        return CheckResult(
            name=f"content_image_{topic}", passed=passed, severity=severity,
            message=msg, events=self.events, detail=detail)


class FullFrameMixin:
    """全帧极速检测入口，由 ContentFilter 在 image_fullframe+unpack 时调用。"""

    def _check_image_fullframe(self, topic: str, ti,
                               frames_iter: Iterator[Tuple[int, float, Any]]) -> CheckResult:
        import time as _t
        t0 = _t.perf_counter()
        analyzer = FrameQualityAnalyzer(self.config)
        analyzer.is_depth = "depth" in topic.lower()
        n = 0
        for idx, ts, payload in frames_iter:
            analyzer.feed(idx, ts, payload)
            n += 1
        result = analyzer.report(topic, ti)
        result.detail["elapsed_sec"] = round(_t.perf_counter() - t0, 4)
        result.detail["decoded_count"] = n
        return result
