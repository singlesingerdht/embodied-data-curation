"""SAM3 feature extraction for dataset initial frames."""
from __future__ import annotations

import math
from contextlib import nullcontext
from typing import Any

import numpy as np
from PIL import Image


def mask_geometry(mask: np.ndarray) -> dict[str, Any]:
    """Return centroid, area, and the unsigned PCA principal axis of a mask.

    ``principal_axis_deg`` uses image coordinates (x right, y down) and is
    canonicalized to [0, 180), because a PCA axis has no head/tail direction.
    ``axis_anisotropy`` approaches zero for round/isotropic masks, whose angle
    should therefore be interpreted cautiously.
    """
    ys, xs = np.where(mask)
    if xs.size == 0:
        raise ValueError("mask 为空")
    cx = float(xs.mean())
    cy = float(ys.mean())
    if xs.size < 2:
        return {
            "centroid": (cx, cy),
            "area": int(xs.size),
            "principal_axis_deg": 0.0,
            "axis_anisotropy": 0.0,
        }

    centered = np.column_stack((xs - cx, ys - cy)).astype(np.float64)
    covariance = centered.T @ centered / max(len(centered) - 1, 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    major = max(float(eigenvalues[order[0]]), 0.0)
    minor = max(float(eigenvalues[order[1]]), 0.0)
    axis = eigenvectors[:, order[0]]
    angle = math.degrees(math.atan2(float(axis[1]), float(axis[0]))) % 180.0
    anisotropy = (major - minor) / (major + minor) if major + minor > 0 else 0.0
    return {
        "centroid": (cx, cy),
        "area": int(xs.size),
        "principal_axis_deg": round(angle, 6),
        "axis_anisotropy": round(anisotropy, 6),
    }


class Sam3DependencyMissing(ImportError):
    """SAM3 模型环境缺失 (依赖问题, 非数据问题)。"""


class Sam3InitialStateExtractor:
    """Load SAM3 once and run one independent text prompt per category."""

    def __init__(
        self,
        config: dict[str, Any],
        checkpoint: str | None = None,
        device: str = "cuda",
    ) -> None:
        try:
            import torch
            from sam3.model.sam3_image_processor import Sam3Processor
            from sam3.model_builder import build_sam3_image_model
        except ImportError as exc:
            raise Sam3DependencyMissing(
                "无法导入 SAM3；请在推理环境安装 sam3"
            ) from exc

        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("请求使用 CUDA，但当前环境不可用")
        if not checkpoint:
            raise ValueError("An explicit local SAM3 checkpoint is required")
        self.torch = torch
        self.device = device
        self.config = config
        self.nms_iou = float(config["nms_iou"])
        self.model = build_sam3_image_model(
            device=device,
            checkpoint_path=checkpoint,
            load_from_HF=False,
            compile=False,
        )
        self.processor = Sam3Processor(self.model, device=device)

    def _instances_from_state(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        from torchvision.ops import nms

        masks_t = state["masks"].squeeze(1)
        boxes_t = state["boxes"]
        scores_t = state["scores"]
        if len(scores_t) == 0:
            return []
        if self.nms_iou < 1.0:
            keep = nms(boxes_t.float(), scores_t.float(), self.nms_iou)
            masks_t, boxes_t, scores_t = masks_t[keep], boxes_t[keep], scores_t[keep]

        masks = masks_t.detach().cpu().numpy().astype(bool)
        boxes = boxes_t.detach().cpu().numpy()
        scores = scores_t.detach().float().cpu().numpy()
        height = int(state["original_height"])
        width = int(state["original_width"])
        instances: list[dict[str, Any]] = []
        for mask, box, confidence in zip(masks, boxes, scores):
            if mask.any():
                geometry = mask_geometry(mask)
                cx, cy = geometry["centroid"]
                area = geometry["area"]
                axis_deg = geometry["principal_axis_deg"]
                anisotropy = geometry["axis_anisotropy"]
            else:
                x0, y0, x1, y1 = (float(v) for v in box)
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                area = 0
                axis_deg = 0.0
                anisotropy = 0.0
            x0, y0, x1, y1 = (float(v) for v in box)
            instances.append(
                {
                    "confidence": round(float(confidence), 6),
                    "bbox_xyxy": [round(v, 3) for v in (x0, y0, x1, y1)],
                    "centroid_px": [round(cx, 3), round(cy, 3)],
                    "centroid_norm": [
                        round(cx / max(width, 1), 8),
                        round(cy / max(height, 1), 8),
                    ],
                    "area_px": area,
                    "area_ratio": round(area / max(width * height, 1), 8),
                    "principal_axis_deg": axis_deg,
                    "axis_anisotropy": anisotropy,
                }
            )
        if getattr(self, "retain_masks", False):
            for instance, mask in zip(instances, masks):
                instance["_mask"] = mask
        return instances

    def extract_image(self, image: Image.Image) -> dict[str, Any]:
        autocast = (
            self.torch.autocast(device_type="cuda", dtype=self.torch.bfloat16)
            if self.device == "cuda"
            else nullcontext()
        )
        with self.torch.inference_mode(), autocast:
            state = self.processor.set_image(image)
            categories: dict[str, Any] = {}
            for obj in self.config["objects"]:
                self.processor.reset_all_prompts(state)
                self.processor.set_confidence_threshold(
                    obj["confidence_threshold"]
                )
                state = self.processor.set_text_prompt(obj["prompt"], state)
                categories[obj["name"]] = {
                    "prompt": obj["prompt"],
                    "confidence_threshold": obj["confidence_threshold"],
                    "instances": self._instances_from_state(state),
                }
        return {
            "width": image.width,
            "height": image.height,
            "categories": categories,
        }
