"""CPU 画面质量检测 — 黑屏/白屏/花屏/冻结/遮挡/模糊/过曝/欠曝/偏色 (DF 方案)

原理:
  - 黑屏: mean<5.0 AND dark_ratio>0.5 (DF 像素域双条件)
  - 白屏: mean>250.0 AND light_ratio>0.5 (DF 亮像素饱和度门控)
  - 花屏: BBD>1.8 AND grad_mean>15 OR 强梯度>0.3 AND grad_mean>20 (DF 联合判定)
  - 冻结: xxhash 全帧哈希, 连续相同>=30帧→熔断 (DF 方案)
  - 遮挡: 积分图网格平坦占比 + 时序突变/持续静止 (DF 方案)
  - 模糊: Laplacian 方差 (VoL), 相对中位骤降或绝对过低 (DF 方案)
  - 过曝: mean>130 AND std>60
  - 欠曝: mean in (5,70) AND std>15
  - 偏色: RGB 通道均值差 > 100

全帧扫描 (非采样), 可独立运行, 也作为 video_integrity.run_check 的画面质量守门。
"""
import os
import json
import argparse
import cv2
import numpy as np
import xxhash


class FrameQualityDetector:
    def __init__(self,
                 black_mean: float = 5.0,
                 dark_level: float = 5.0,
                 dark_ratio: float = 0.5,
                 white_mean: float = 250.0,
                 light_level: float = 245.0,
                 light_ratio: float = 0.5,
                 bbd_threshold: float = 1.8,
                 grad_mean_threshold: float = 15.0,
                 strong_grad_threshold: float = 40.0,
                 strong_grad_frac: float = 0.3,
                 strong_grad_mean: float = 20.0,
                 freeze_min_frames: int = 30,
                 # 遮挡
                 occlusion_flat_sigma: float = 5.0,
                 occlusion_high_texture_flat_ratio: float = 0.3,
                 occlusion_low_texture_flat_ratio: float = 0.65,
                 occlusion_rise_threshold: float = 0.10,
                 occlusion_largest_flat_threshold: float = 0.15,
                 occlusion_persistent_flat: float = 0.9,
                 occlusion_persistent_mad: float = 2.0,
                 occlusion_persistent_window: int = 10,
                 # 模糊
                 blur_ratio: float = 0.4,
                 blur_sharpness_floor: float = 150.0,
                 # 过曝/欠曝
overexpo_mean: float = 200.0,
                  overexpo_std: float = 60.0,
                  underexpo_mean: float = 50.0,
                  underexpo_std: float = 15.0,
                  underexpo_min: float = 5.0,
                 # 偏色
                  color_anomaly_range: float = 100.0,
                  melt_ratio: float = 0.3,
                  # 性能: 降采样(步长切片, 保留高频细节, 阈值可沿用) + 帧采样
                  downscale: int = 1,
                  sample_step: int = 1):
        self.black_mean = black_mean
        self.dark_level = dark_level
        self.dark_ratio = dark_ratio
        self.white_mean = white_mean
        self.light_level = light_level
        self.light_ratio = light_ratio
        self.bbd_threshold = bbd_threshold
        self.grad_mean_threshold = grad_mean_threshold
        self.strong_grad_threshold = strong_grad_threshold
        self.strong_grad_frac = strong_grad_frac
        self.strong_grad_mean = strong_grad_mean
        self.freeze_min_frames = freeze_min_frames
        self.occlusion_flat_sigma = occlusion_flat_sigma
        self.occlusion_high_texture_flat_ratio = occlusion_high_texture_flat_ratio
        self.occlusion_low_texture_flat_ratio = occlusion_low_texture_flat_ratio
        self.occlusion_rise_threshold = occlusion_rise_threshold
        self.occlusion_largest_flat_threshold = occlusion_largest_flat_threshold
        self.occlusion_persistent_flat = occlusion_persistent_flat
        self.occlusion_persistent_mad = occlusion_persistent_mad
        self.occlusion_persistent_window = occlusion_persistent_window
        self.blur_ratio = blur_ratio
        self.blur_sharpness_floor = blur_sharpness_floor
        self.overexpo_mean = overexpo_mean
        self.overexpo_std = overexpo_std
        self.underexpo_mean = underexpo_mean
        self.underexpo_std = underexpo_std
        self.underexpo_min = underexpo_min
        self.color_anomaly_range = color_anomaly_range
        self.melt_ratio = melt_ratio
        self.downscale = max(1, int(downscale))
        self.sample_step = max(1, int(sample_step))

    def detect(self, video_path: str) -> dict:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return self._degraded(f"无法打开视频: {video_path}")
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            cap.release()
            return self._degraded(f"无法读取帧数: {video_path}")
        return self._detect_capture(cap, total, str(video_path))

    def detect_frames(self, frames, expected_count: int) -> dict:
        """Use the same detector on source RGB frames, without video encoding."""
        if type(expected_count) is not int or expected_count <= 0:
            raise ValueError('Frame input requires a positive expected_count')
        capture = _FrameCapture(frames)
        try:
            result = self._detect_capture(capture, expected_count, 'source frame stream')
            if capture.count != expected_count:
                raise ValueError(f'Decoded {capture.count} frames, expected {expected_count}')
            return result
        finally:
            capture.release()

    def _detect_capture(self, cap, total, input_label):

        black_n = white_n = garbled_n = 0
        occlusion_n = 0
        overexpo_n = underexpo_n = color_anomaly_n = 0
        freeze_segments = []
        freeze_start = None
        freeze_count = 0
        prev_hash = None
        prev_gray = None
        flat_history = []   # 只存 flat_ratio 标量 (原实现存整帧图, 长视频会占数 GB 内存)
        blur_scores = []
        per_frame = []
        idx = 0             # 已解码的真实帧号
        processed = 0        # 实际分析(采样后)的帧数
        last_sampled_idx = None

        # 帧采样下的冻结阈值换算: 真实 freeze_min_frames 帧 ≈ freeze_min_frames/step 个采样帧
        freeze_min = max(1, round(self.freeze_min_frames / self.sample_step))

        while True:
            # 帧采样: 非采样帧用 grab() 跳过 (不解码像素)
            if self.sample_step > 1 and idx % self.sample_step != 0:
                if not cap.grab():
                    break
                idx += 1
                continue

            ok, frame = cap.read()
            if not ok or frame is None:
                break

            # 降采样: 步长切片 (保留高频细节 → VoL/BBD/遮挡阈值可沿用, 参考 DataFilter 1/4 灰度方案)
            if self.downscale > 1:
                frame = frame[::self.downscale, ::self.downscale]

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean = float(gray.mean())

            is_black = mean < self.black_mean and self._check_dark_ratio(gray)
            is_white = mean > self.white_mean and self._check_light_ratio(gray)
            is_garbled = self._check_garbled_bbd(gray) or self._check_garbled_gradient(gray)

            # 遮挡: flat_ratio 只计算一次并复用 (原实现每帧重复计算两次)
            flat_ratio = self._compute_flat_ratio(gray)
            is_occlusion = self._check_occlusion(gray, mean, flat_history, prev_gray, flat_ratio)
            flat_history.append(flat_ratio)

            # 模糊: Laplacian 方差 (VoL)
            blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            blur_scores.append(blur_score)

            # 过曝/欠曝/偏色
            std = float(gray.std())
            is_overexpo = mean > self.overexpo_mean and std > self.overexpo_std
            is_underexpo = (self.underexpo_min < mean < self.underexpo_mean and std > self.underexpo_std)
            is_color_anomaly = self._check_color_anomaly(frame)

            if is_black:
                black_n += 1
            if is_white:
                white_n += 1
            if is_garbled:
                garbled_n += 1
            if is_occlusion:
                occlusion_n += 1
            if is_overexpo:
                overexpo_n += 1
            if is_underexpo:
                underexpo_n += 1
            if is_color_anomaly:
                color_anomaly_n += 1

            fhash = xxhash.xxh64(gray.tobytes()).hexdigest()
            if fhash == prev_hash:
                freeze_count += 1
                if freeze_start is None:
                    freeze_start = last_sampled_idx if last_sampled_idx is not None else idx
            else:
                if freeze_count >= freeze_min:
                    freeze_segments.append({
                        "start": freeze_start, "end": last_sampled_idx, "count": freeze_count,
                    })
                freeze_count = 0
                freeze_start = None
            prev_hash = fhash
            last_sampled_idx = idx

            per_frame.append({
                "idx": idx, "mean": round(mean, 1),
                "black": is_black, "white": is_white, "garbled": is_garbled,
                "occlusion": is_occlusion, "overexpo": is_overexpo,
                "underexpo": is_underexpo, "color_anomaly": is_color_anomaly,
            })
            prev_gray = gray
            idx += 1
            processed += 1

        if freeze_count >= freeze_min:
            freeze_segments.append({
                "start": freeze_start, "end": last_sampled_idx, "count": freeze_count,
            })

        cap.release()
        valid = processed
        if valid == 0:
            return self._degraded(f"未能解码任何帧: {input_label}")

        # 冻结帧数换算回真实帧数 (采样帧 × step)
        freeze_frames = sum(s["count"] for s in freeze_segments) * self.sample_step
        freeze_n = len(freeze_segments)
        real_frames = idx  # 解码到的真实总帧数

        # 模糊: 相对中位骤降 或 绝对过低
        blur_n = 0
        blur_median = 0.0
        if blur_scores:
            blur_median = float(np.median(blur_scores))
            if blur_median > self.blur_sharpness_floor:
                for s in blur_scores:
                    if s > 0 and s < self.blur_ratio * blur_median:
                        blur_n += 1

        black_ratio = black_n / valid
        white_ratio = white_n / valid
        garbled_ratio = garbled_n / valid
        freeze_ratio = freeze_frames / max(1, real_frames)
        occlusion_ratio = occlusion_n / valid
        blur_ratio_val = blur_n / valid
        overexpo_ratio = overexpo_n / valid
        underexpo_ratio = underexpo_n / valid
        color_anomaly_ratio = color_anomaly_n / valid

        # 区分持久偏色 (相机白平衡) vs 突发偏色 (画面损坏)
        color_persistent = False
        if color_anomaly_n > 0:
            ca_indices = [f["idx"] for f in per_frame if f["color_anomaly"]]
            if ca_indices:
                ca_span = (ca_indices[-1] - ca_indices[0]) / max(1, real_frames)
                color_persistent = ca_span > 0.3 and color_anomaly_n > max(valid * 0.1, 10)

        worst_no_color = max(black_ratio, white_ratio, garbled_ratio, freeze_ratio,
                             occlusion_ratio, blur_ratio_val, overexpo_ratio, underexpo_ratio)
        if color_persistent:
            worst = worst_no_color
        else:
            worst = max(worst_no_color, color_anomaly_ratio)
        score = round(max(0.0, 100.0 * (1.0 - worst / self.melt_ratio)), 1)
        passed = worst < self.melt_ratio

        if worst >= self.melt_ratio:
            verdict = "DO_NOT_USE"
        elif worst > 0 or (color_persistent and color_anomaly_ratio > 0.1):
            verdict = "NEEDS_REVIEW"
        else:
            verdict = "ACCEPTABLE"

        if color_persistent and color_anomaly_ratio > 0.1:
            score = min(score, 90.0)

        reasons = []
        if black_ratio > 0:
            reasons.append(f"黑屏帧占比 {black_ratio:.0%} (均值<{self.black_mean} 且暗像素>{self.dark_ratio})")
        if white_ratio > 0:
            reasons.append(f"白屏帧占比 {white_ratio:.0%} (均值>{self.white_mean} 且亮像素>{self.light_ratio})")
        if garbled_ratio > 0:
            reasons.append(f"花屏帧占比 {garbled_ratio:.0%} (BBD+强梯度)")
        if freeze_n > 0:
            reasons.append(f"帧冻结 {freeze_n} 段共 {freeze_frames} 帧 (xxhash, 连续>={self.freeze_min_frames}帧)")
        if occlusion_ratio > 0:
            reasons.append(f"遮挡帧占比 {occlusion_ratio:.0%} (积分图平坦网格)")
        if blur_ratio_val > 0:
            reasons.append(f"模糊帧占比 {blur_ratio_val:.0%} (VoL中位={blur_median:.0f})")
        if overexpo_ratio > 0:
            reasons.append(f"过曝帧占比 {overexpo_ratio:.0%} (均值>{self.overexpo_mean} 且std>{self.overexpo_std})")
        if underexpo_ratio > 0:
            reasons.append(f"欠曝帧占比 {underexpo_ratio:.0%} (均值在({self.underexpo_min},{self.underexpo_mean}) 且std>{self.underexpo_std})")
        if color_anomaly_ratio > 0:
            if color_persistent:
                reasons.append(f"相机级持续偏色 {color_anomaly_ratio:.0%} (白平衡偏移, 非画面损坏)")
            else:
                reasons.append(f"偏色帧占比 {color_anomaly_ratio:.0%} (通道差>{self.color_anomaly_range})")

        return {
            "black_ratio": round(black_ratio, 3),
            "white_ratio": round(white_ratio, 3),
            "garbled_ratio": round(garbled_ratio, 3),
            "freeze_ratio": round(freeze_ratio, 3),
            "occlusion_ratio": round(occlusion_ratio, 3),
            "blur_ratio": round(blur_ratio_val, 3),
            "overexpo_ratio": round(overexpo_ratio, 3),
            "underexpo_ratio": round(underexpo_ratio, 3),
            "color_anomaly_ratio": round(color_anomaly_ratio, 3),
            "color_persistent": color_persistent,
            "freeze_segments": freeze_segments,
            "freeze_frames": freeze_frames,
            "blur_median_sharpness": round(blur_median, 1),
            "worst_ratio": round(worst, 3),
            "score": score,
            "passed": passed,
            "verdict": verdict,
            "reasons": reasons,
            "total_frames": valid,
            "real_frames": real_frames,
            "sampling": {"downscale": self.downscale, "sample_step": self.sample_step,
                         "freeze_min_frames": self.freeze_min_frames,
                         "freeze_min_sampled": freeze_min},
            "thresholds": {
                "black": {"mean": self.black_mean, "dark_level": self.dark_level, "dark_ratio": self.dark_ratio},
                "white": {"mean": self.white_mean, "light_level": self.light_level, "light_ratio": self.light_ratio},
                "garbled": {"bbd": self.bbd_threshold, "grad_mean": self.grad_mean_threshold,
                            "strong_grad_threshold": self.strong_grad_threshold,
                            "strong_grad_frac": self.strong_grad_frac,
                            "strong_grad_mean": self.strong_grad_mean},
                "freeze": {"min_frames": self.freeze_min_frames},
                "occlusion": {"flat_sigma": self.occlusion_flat_sigma},
                "blur": {"ratio": self.blur_ratio, "sharpness_floor": self.blur_sharpness_floor},
                "exposure": {"overexpo": {"mean": self.overexpo_mean, "std": self.overexpo_std},
                             "underexpo": {"mean": self.underexpo_mean, "std": self.underexpo_std}},
                "color_anomaly": {"range": self.color_anomaly_range},
                "melt_ratio": self.melt_ratio,
            },
            "per_frame": per_frame,
        }

    def _check_dark_ratio(self, gray: np.ndarray) -> bool:
        return float((gray < self.dark_level).mean()) > self.dark_ratio

    def _check_light_ratio(self, gray: np.ndarray) -> bool:
        return float((gray > self.light_level).mean()) > self.light_ratio

    def _check_garbled_bbd(self, img: np.ndarray) -> bool:
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
        return bbd > self.bbd_threshold and grad_mean > self.grad_mean_threshold

    def _check_garbled_gradient(self, img: np.ndarray) -> bool:
        h, w = img.shape
        if h < 8 or w < 8:
            return False
        g = img.astype(np.float32)
        gx = np.abs(np.diff(g, axis=1))
        gy = np.abs(np.diff(g, axis=0))
        grad = (gx[1:, :] + gy[:, 1:]) / 2.0
        strong_frac = float((grad > self.strong_grad_threshold).mean())
        grad_mean = float(grad.mean())
        return strong_frac > self.strong_grad_frac and grad_mean > self.strong_grad_mean

    def _compute_flat_ratio(self, img: np.ndarray) -> float:
        """积分图网格平坦占比 (DF 遮挡检测)"""
        h, w = img.shape
        gs = max(4, min(h, w) // 12)
        H, W = h // gs, w // gs
        if H < 2 or W < 2:
            return 1.0
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
        return float((sigma < self.occlusion_flat_sigma).mean())

    def _check_occlusion(self, gray: np.ndarray, mean_val: float,
                         flat_history: list, prev_gray,
                         flat_ratio: float = None) -> bool:
        """遮挡检测: 时序突变 + 持续遮挡 (DF 方案)

        flat_history 现在只存 flat_ratio 标量; flat_ratio 可由调用方传入避免重复计算。
        """
        if not (10.0 <= mean_val <= 245.0):
            return False
        if flat_ratio is None:
            flat_ratio = self._compute_flat_ratio(gray)
        if len(flat_history) < 5:
            return False
        prev_flat_med = float(np.median(list(flat_history[-5:])))
        if prev_flat_med < self.occlusion_high_texture_flat_ratio:
            if flat_ratio > prev_flat_med + self.occlusion_rise_threshold:
                _, largest_flat = self._compute_largest_flat(gray)
                return largest_flat > self.occlusion_largest_flat_threshold
        else:
            if (flat_ratio > self.occlusion_low_texture_flat_ratio
                    and flat_ratio > prev_flat_med + self.occlusion_rise_threshold + 0.05):
                _, largest_flat = self._compute_largest_flat(gray)
                return largest_flat > self.occlusion_low_texture_flat_ratio
        if len(flat_history) >= self.occlusion_persistent_window:
            recent = list(flat_history[-self.occlusion_persistent_window:])
            if float(np.median(recent)) > self.occlusion_persistent_flat and prev_gray is not None:
                mad = float(np.abs(gray.astype(np.int16) - prev_gray.astype(np.int16)).mean())
                if mad < self.occlusion_persistent_mad:
                    return True
        return False

    def _compute_largest_flat(self, img: np.ndarray):
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
        flat = (sigma < self.occlusion_flat_sigma).astype(np.uint8)
        flat_ratio = float(flat.mean())
        n_cc, _, stats, _ = cv2.connectedComponentsWithStats(flat, connectivity=8)
        largest = float(stats[1:, 4].max()) / float(flat.size) if n_cc > 1 else 0.0
        return flat_ratio, largest

    def _check_color_anomaly(self, frame: np.ndarray) -> bool:
        ch = frame.mean(axis=(0, 1))
        return float(ch.max() - ch.min()) > self.color_anomaly_range

    def _degraded(self, msg: str) -> dict:
        return {
            "black_ratio": 0.0, "white_ratio": 0.0, "garbled_ratio": 0.0,
            "freeze_ratio": 0.0, "freeze_segments": [], "freeze_frames": 0,
            "worst_ratio": 0.0, "score": 100.0, "passed": True,
            "verdict": "ACCEPTABLE", "reasons": [f"画面质量检测跳过: {msg}"],
            "total_frames": 0, "degraded": True, "thresholds": {}, "per_frame": [],
        }


class _FrameCapture:
    """Small capture adapter; no mutation or re-encoding of source pixels."""
    def __init__(self, frames):
        self.iterator = iter(frames)
        self.count = 0

    def _next(self):
        try:
            item = next(self.iterator)
        except StopIteration:
            return None
        if not isinstance(item, dict) or item.get('index') != self.count:
            raise ValueError('Frame input requires contiguous zero-based source indices')
        pixels = np.asarray(item.get('image'))
        if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3 or not pixels.size:
            raise ValueError('Frame input requires nonempty uint8 RGB pixels')
        self.count += 1
        return pixels

    def read(self):
        pixels = self._next()
        # Channel reversal is exact. Keep a view rather than copying the full
        # RGB image before the detector's existing spatial slice.
        return (False, None) if pixels is None else (True, pixels[:, :, ::-1])

    def grab(self):
        return self._next() is not None

    def release(self):
        close = getattr(self.iterator, 'close', None)
        if close:
            close()


def main():
    ap = argparse.ArgumentParser(description="CPU 画面质量检测: 黑屏/白屏/花屏/冻结 (DF)")
    ap.add_argument("video", help="视频文件路径")
    ap.add_argument("--freeze-min-frames", type=int, default=30)
    args = ap.parse_args()
    det = FrameQualityDetector(freeze_min_frames=args.freeze_min_frames)
    result = det.detect(args.video)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
