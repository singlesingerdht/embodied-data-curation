"""Depth-backed geometry for single-support-plane initial scenes."""
from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np


def backproject(u, v, z, intrinsics: dict) -> np.ndarray:
    fx, fy = float(intrinsics["fx"]), float(intrinsics["fy"])
    cx, cy = float(intrinsics["cx"]), float(intrinsics["cy"])
    return np.column_stack(((u - cx) * z / fx, (v - cy) * z / fy, z))


def fit_support_plane(depth_m: np.ndarray, masks: Iterable[np.ndarray],
                      intrinsics: dict, config: dict, seed: int = 0) -> dict:
    """Fit the dominant support plane with RANSAC, then refine using SVD."""
    h, w = depth_m.shape
    roi = config.get("roi_norm", [0.04, 0.18, 0.96, 0.82])
    x0, y0, x1, y1 = roi
    valid = np.zeros((h, w), bool)
    valid[int(y0*h):int(y1*h), int(x0*w):int(x1*w)] = True
    z_min, z_max = config.get("depth_range_m", [0.25, 3.5])
    valid &= np.isfinite(depth_m) & (depth_m > z_min) & (depth_m < z_max)
    kernel = int(config.get("mask_dilation_px", 19))
    for mask in masks:
        valid &= ~cv2.dilate(mask.astype(np.uint8), np.ones((kernel, kernel), np.uint8)).astype(bool)
    v, u = np.nonzero(valid)
    if len(u) < 100:
        raise ValueError(f"支撑面候选点不足: {len(u)}")
    rng = np.random.default_rng(seed)
    take = rng.choice(len(u), min(int(config.get("max_points", 14000)), len(u)), replace=False)
    u, v = u[take], v[take]
    pts = backproject(u, v, depth_m[v, u], intrinsics)
    threshold = float(config.get("ransac_threshold_m", 0.012))
    best = np.zeros(len(pts), bool)
    for _ in range(int(config.get("ransac_iterations", 500))):
        sample = pts[rng.choice(len(pts), 3, replace=False)]
        normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
        norm = np.linalg.norm(normal)
        if norm < 1e-8:
            continue
        normal /= norm
        offset = -normal @ sample[0]
        inside = np.abs(pts @ normal + offset) < threshold
        if inside.sum() > best.sum():
            best = inside
    if best.sum() < 50:
        raise ValueError(f"RANSAC 支撑面内点不足: {int(best.sum())}")
    table = pts[best]
    center = table.mean(axis=0)
    _, _, vh = np.linalg.svd(table - center, full_matrices=False)
    normal = vh[-1] / np.linalg.norm(vh[-1])
    offset = float(-normal @ center)
    residual = np.abs(pts @ normal + offset)
    return {
        "normal_camera": normal.tolist(), "offset": offset,
        "candidate_points": int(len(pts)), "inlier_points": int(best.sum()),
        "inlier_ratio": float(best.mean()),
        "median_residual_m": float(np.median(residual[best])),
        "p95_residual_m": float(np.percentile(residual[best], 95)),
        "ransac_threshold_m": threshold,
    }


def object_geometry(mask: np.ndarray, depth_m: np.ndarray, plane: dict,
                    intrinsics: dict) -> dict:
    v, u = np.nonzero(mask)
    z = depth_m[v, u]
    keep = np.isfinite(z) & (z > 0.2) & (z < 4.0)
    u, v, z = u[keep], v[keep], z[keep]
    if len(z) == 0:
        raise ValueError("物体 mask 内没有有效深度")
    pts = backproject(u, v, z, intrinsics)
    normal = np.asarray(plane["normal_camera"], float)
    signed = pts @ normal + float(plane["offset"])
    if np.median(signed) < 0:
        signed = -signed
    return {
        "camera_xyz_m": np.median(pts, axis=0).tolist(),
        "height_above_support_m": float(np.median(signed)),
        "height_p75_m": float(np.percentile(signed, 75)),
        "valid_depth_pixels": int(len(z)),
        "mask_pixels": int(mask.sum()),
    }


def mask_iou(left: np.ndarray, right: np.ndarray) -> float:
    union = np.logical_or(left, right).sum()
    return float(np.logical_and(left, right).sum() / union) if union else 0.0


def mask_center(mask: np.ndarray) -> tuple[float, float]:
    y, x = np.nonzero(mask)
    return float(np.median(x)), float(np.median(y))


def select_distinct(candidates: list[dict], count: int, max_iou: float = 0.25) -> list[dict]:
    selected = []
    for candidate in candidates:
        if all(mask_iou(candidate["mask"], item["mask"]) < max_iou for item in selected):
            selected.append(candidate)
            if len(selected) == count:
                break
    return selected
