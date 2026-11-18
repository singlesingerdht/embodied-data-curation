"""实例外观 ReID 与多帧重新确认。

SAM3 只产生语义候选；本模块用实例模板区分外观相近的 teapot/kettle
及其盖子，并在连续多次稳定命中后才允许切换跟踪中心。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np


@dataclass(frozen=True)
class AppearanceConfig:
    model_name: str = "facebook/dinov2-small"
    cache_dir: str = None
    # 当前相机大视角变化下，真实 teapot 候选实测约 0.66--0.71；
    # 身份安全主要由竞争 margin 与三次确认共同保证。
    min_similarity: float = 0.65
    min_margin: float = 0.08
    confirm_frames: int = 3
    position_tolerance_m: float = 0.05
    moving_position_tolerance_m: float = 0.15
    motion_direction_cosine: float = 0.5
    template_ema: float = 0.05


@dataclass(frozen=True)
class CandidateScore:
    index: int
    similarity: float
    competitor_similarity: float

    @property
    def margin(self) -> float:
        return self.similarity - self.competitor_similarity


@dataclass
class PendingCandidate:
    center: np.ndarray
    embedding: np.ndarray
    count: int = 1
    velocity: np.ndarray | None = None
    moving: bool = False


class DINOv2Encoder:
    """延迟加载的 DINOv2 crop 编码器。"""

    def __init__(self, model_name: str, cache_dir: str,
                 device: str | None = None):
        import torch
        from transformers import AutoImageProcessor, AutoModel

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        source, local_only = self._resolve_local_snapshot(model_name, cache_dir)
        self.processor = AutoImageProcessor.from_pretrained(
            source, cache_dir=cache_dir, local_files_only=local_only)
        self.model = AutoModel.from_pretrained(
            source, cache_dir=cache_dir,
            local_files_only=local_only).to(self.device).eval()

    @staticmethod
    def _resolve_local_snapshot(model_name: str, cache_dir: str) -> tuple[str, bool]:
        """优先把 HF 模型 ID 解析为已有 snapshot，彻底避免联网探测。"""
        direct = Path(model_name)
        if direct.is_dir():
            return str(direct), True
        repo = Path(cache_dir) / ("models--" + model_name.replace("/", "--"))
        ref = repo / "refs" / "main"
        if ref.is_file():
            revision = ref.read_text().strip()
            snapshot = repo / "snapshots" / revision
            if ((snapshot / "config.json").is_file()
                    and (snapshot / "preprocessor_config.json").is_file()):
                return str(snapshot), True
        return model_name, False

    def __call__(self, crops: Sequence[np.ndarray]) -> np.ndarray:
        if not crops:
            return np.zeros((0, 384), np.float32)
        inputs = self.processor(images=list(crops), return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with self.torch.inference_mode():
            out = self.model(**inputs).last_hidden_state[:, 0]
            out = self.torch.nn.functional.normalize(out, dim=-1)
        return out.float().cpu().numpy()


class AppearanceMatcher:
    def __init__(self, config: AppearanceConfig | None = None,
                 encoder: Callable[[Sequence[np.ndarray]], np.ndarray] | None = None):
        self.cfg = config or AppearanceConfig()
        self.encoder = encoder or DINOv2Encoder(
            self.cfg.model_name, self.cfg.cache_dir)
        self.templates: dict[str, np.ndarray] = {}
        self.pending: dict[str, PendingCandidate] = {}

    @staticmethod
    def masked_crop(rgb: np.ndarray, mask: np.ndarray,
                    padding: float = 0.12) -> np.ndarray:
        m = np.asarray(mask, bool)
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            return np.zeros((32, 32, 3), np.uint8)
        h, w = m.shape
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        pad = int(max(x1 - x0, y1 - y0) * padding)
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(w, x1 + pad), min(h, y1 + pad)
        crop = np.asarray(rgb[y0:y1, x0:x1], np.uint8).copy()
        cm = m[y0:y1, x0:x1]
        crop[~cm] = 127
        return cv2.resize(crop, (224, 224), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _normalize(v: np.ndarray) -> np.ndarray:
        v = np.asarray(v, np.float32).reshape(-1)
        return v / max(float(np.linalg.norm(v)), 1e-8)

    def register_template(self, eid: str, rgb: np.ndarray,
                          mask: np.ndarray) -> None:
        emb = self.encoder([self.masked_crop(rgb, mask)])[0]
        self.templates[eid] = self._normalize(emb)

    def score_candidates(self, eid: str, rgb: np.ndarray,
                         masks: Sequence[np.ndarray],
                         competitors: Sequence[str] = ()) -> tuple[list[CandidateScore], np.ndarray]:
        if eid not in self.templates or not masks:
            return [], np.zeros((0, 0), np.float32)
        embs = np.asarray(self.encoder(
            [self.masked_crop(rgb, m) for m in masks]), np.float32)
        embs = np.stack([self._normalize(e) for e in embs])
        target = self.templates[eid]
        competitor_templates = [self.templates[x] for x in competitors
                                if x in self.templates and x != eid]
        scores: list[CandidateScore] = []
        for i, emb in enumerate(embs):
            sim = float(emb @ target)
            comp = max((float(emb @ t) for t in competitor_templates),
                       default=-1.0)
            scores.append(CandidateScore(i, sim, comp))
        scores.sort(key=lambda x: (x.similarity, x.margin), reverse=True)
        return scores, embs

    def passes(self, score: CandidateScore) -> bool:
        return (score.similarity >= self.cfg.min_similarity
                and score.margin >= self.cfg.min_margin)

    def confirm(self, eid: str, center: np.ndarray,
                embedding: np.ndarray) -> bool:
        c = np.asarray(center, np.float64)
        emb = self._normalize(embedding)
        old = self.pending.get(eid)
        if old is None:
            self.pending[eid] = PendingCandidate(c.copy(), emb.copy(), 1)
            return self.cfg.confirm_frames <= 1
        delta = c - old.center
        step = float(np.linalg.norm(delta))
        stable = step <= self.cfg.position_tolerance_m
        motion_ok = False
        if not stable and step <= self.cfg.moving_position_tolerance_m:
            if old.velocity is None:
                # 第二个候选建立运动假设；第三个候选再验证方向。
                motion_ok = True
            else:
                vnorm = float(np.linalg.norm(old.velocity))
                cosine = float(delta @ old.velocity) / max(step * vnorm, 1e-8)
                prediction_error = float(np.linalg.norm(delta - old.velocity))
                motion_ok = (cosine >= self.cfg.motion_direction_cosine
                             and prediction_error <= max(0.08, 0.75 * vnorm))
        if not stable and not motion_ok:
            self.pending[eid] = PendingCandidate(c.copy(), emb.copy(), 1)
            return self.cfg.confirm_frames <= 1
        old.velocity = delta.copy() if motion_ok else old.velocity
        old.moving = old.moving or motion_ok
        # 运动候选必须保留最后位置，不能用 EMA 中心拖慢预测；静止候选可平滑。
        old.center = c.copy() if old.moving else 0.5 * old.center + 0.5 * c
        old.embedding = self._normalize(old.embedding + emb)
        old.count += 1
        return old.count >= self.cfg.confirm_frames

    def update_template(self, eid: str, embedding: np.ndarray,
                        trusted: bool) -> None:
        if not trusted or eid not in self.templates:
            return
        alpha = self.cfg.template_ema
        self.templates[eid] = self._normalize(
            (1.0 - alpha) * self.templates[eid]
            + alpha * self._normalize(embedding))

    def accept(self, eid: str, embedding: np.ndarray) -> None:
        self.update_template(eid, embedding, trusted=True)
        self.pending.pop(eid, None)

    def reject(self, eid: str) -> None:
        self.pending.pop(eid, None)
