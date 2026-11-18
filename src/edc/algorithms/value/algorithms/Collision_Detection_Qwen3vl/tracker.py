"""Module 2: CoTracker3 2D 点跟踪 + 深度查询 → 3D 椭球更新 (Update 2 五步管线)。

显式范式 (拒绝重型 3D Scene Flow):
  Step1  帧 0 在各实体 SAM3 mask 内均匀采样 N 点
  Step2  CoTracker3 (offline) 一次前向传播得到全视频 (u,v)(t) 轨迹
  Step3  每检测帧: 在 (u,v) 处鲁棒查询深度 Z (k×k 中值, 剔零)
  Step4  小孔模型投影: X=(u-cx)Z/fx, Y=(v-cy)Z/fy, Z=Z
  Step5  A/C 类: 3D 点 → 中值中心 (radii/axes 冻结在帧 0, 碰撞体积不变)

CoTracker3 offline 全局优化, 一次前向得整段轨迹, 比逐帧 LK 稳得多;
点级可见性 (visibility) 用于剔除遮挡帧的脏深度。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

import torch

_TRACKED_PER_ENTITY = 48      # 每实体采样点数
_VIS_TH = 0.5   # CoTracker 可见性门控: 遮挡期预测点不可信, 剔除
_OCCLUDE_ENTER = 0.6           # 可见点占比 < 0.6 → 判遮挡, 冻结中心 (非 bonded)
_OCCLUDE_EXIT  = 0.8           # 恢复跟踪需可见占比回升到 0.8 (滞回防抖动)
_SMOOTH = 0.6                 # 中心指数平滑系数 (新值权重 0.6, 响应快)
_VIDEO_DOWNSAMPLE = 2         # CoTracker 视频帧下采样率 (降显存, 轨迹随后插值回原分辨率)
_VIDEO_DOWNSAMPLE_LARGE = 10 # 大分辨率视频 (1280×720+) 的下采样率
_SPATIAL_DOWNSAMPLE = 2       # 空间下采样 (大视频降分辨率进一步降显存)
_MAX_DOWNSAMPLED_FRAMES = 500 # CoTracker 离线块上限; 约 5000 原始帧 @ ds=10
_CHUNK_OVERLAP = 30           # 块间重叠, 用上一块轨迹在重叠起点续接


def sample_mask_points(mask: np.ndarray, n: int = _TRACKED_PER_ENTITY,
                       seed: int = 0) -> np.ndarray:
    """Step1: mask 内均匀采样 n 个 (u,v) 像素点。"""
    vs, us = np.nonzero(mask)
    if len(us) == 0:
        return np.zeros((0, 2), np.float32)
    rng = np.random.RandomState(seed)
    idx = rng.choice(len(us), min(n, len(us)), replace=False)
    return np.stack([us[idx], vs[idx]], axis=1).astype(np.float32)


class CoTracker3D:
    """CoTracker3 封装: mask 查询点 → 全视频 (T,N,2) 轨迹 + (T,N) 可见性。"""

    def __init__(self, ckpt: str | None = None):
        if not ckpt:
            raise ValueError("Explicit CoTracker checkpoint required")
        from cotracker.predictor import CoTrackerPredictor
        self.model = CoTrackerPredictor(
            checkpoint=ckpt, offline=True, v2=False, window_len=60).cuda().eval()

    @torch.no_grad()
    def track(self, video: np.ndarray, queries_uv: np.ndarray,
              query_frame: int = 0) -> tuple[np.ndarray, np.ndarray]:
        """Step2: 全视频跟踪。

        Args:
            video: (T,H,W,3) uint8
            queries_uv: (N,2) float (u,v) 在 query_frame 帧的坐标
        Returns:
            tracks: (T,N,2) 全视频 (u,v); visibs: (T,N) float [0,1]
        """
        ds = _VIDEO_DOWNSAMPLE
        # 大分辨率视频自适应增大下采样率 (防 OOM)
        h, w = video.shape[1], video.shape[2]
        spatial_ds = 1
        if max(h, w) > 1000:
            ds = _VIDEO_DOWNSAMPLE_LARGE
            spatial_ds = _SPATIAL_DOWNSAMPLE
        video_ds = video[::ds]
        # 空间下采样 (双线性)
        if spatial_ds > 1:
            import cv2
            video_ds = np.stack([
                cv2.resize(f, (w // spatial_ds, h // spatial_ds))
                for f in video_ds
            ])
            queries_uv_ds = queries_uv / spatial_ds
        else:
            queries_uv_ds = queries_uv
        query_ds = query_frame // ds
        if len(video_ds) <= _MAX_DOWNSAMPLED_FRAMES:
            tracks, visibs = self._track_downsampled(
                video_ds, queries_uv_ds, query_ds)
        else:
            tracks, visibs = self._track_downsampled_chunks(
                video_ds, queries_uv_ds, query_ds)
        tracks *= spatial_ds  # 缩放回原分辨率
        # 插值回原分辨率: 线性插值 tracks (阶梯复制在快速运动时产生滞后)
        T_full = len(video)
        tracks_full = np.zeros((T_full, tracks.shape[1], 2), np.float32)
        vis_full = np.zeros((T_full, tracks.shape[1]), np.float32)
        n_ds = len(tracks)
        for j in range(T_full):
            i = j // ds
            frac = (j - i * ds) / ds
            if i + 1 < n_ds:
                tracks_full[j] = tracks[i] * (1 - frac) + tracks[i + 1] * frac
            else:
                tracks_full[j] = tracks[i]
            vis_full[j] = visibs[min(i, n_ds - 1)]
        return tracks_full, vis_full

    def _track_downsampled(self, video: np.ndarray, queries_uv: np.ndarray,
                           query_frame: int = 0) -> tuple[np.ndarray, np.ndarray]:
        """Run one bounded CoTracker offline call."""
        t = torch.from_numpy(np.ascontiguousarray(video)).permute(
            0, 3, 1, 2)[None].cuda().float()
        # The official predictor normalizes 0..255 pixels internally.
        q = np.concatenate([
            np.full((len(queries_uv), 1), query_frame, np.float32), queries_uv
        ], axis=1)
        tracks, visibs = self.model(
            video=t, queries=torch.from_numpy(q)[None].cuda())
        result = tracks[0].cpu().numpy(), visibs[0].cpu().numpy()
        del t, tracks, visibs
        torch.cuda.empty_cache()
        return result

    def _track_downsampled_chunks(self, video: np.ndarray,
                                  queries_uv: np.ndarray,
                                  query_frame: int) -> tuple[np.ndarray, np.ndarray]:
        """Track long videos in overlapping blocks without a full-video tensor.

        The first block contains the requested query frame. Every later block is
        re-queried at its first frame using the preceding block's predicted
        coordinates, so identity/order stay stable while GPU memory is bounded.
        """
        if query_frame != 0:
            raise ValueError("chunked CoTracker currently requires query_frame=0")
        total = len(video)
        step = _MAX_DOWNSAMPLED_FRAMES - _CHUNK_OVERLAP
        all_tracks = np.empty((total, len(queries_uv), 2), np.float32)
        all_visibs = np.empty((total, len(queries_uv)), np.float32)
        start = 0
        current_queries = queries_uv
        while start < total:
            end = min(total, start + _MAX_DOWNSAMPLED_FRAMES)
            print(f"  CoTracker chunk {start}:{end}/{total}", flush=True)
            chunk_tracks, chunk_visibs = self._track_downsampled(
                video[start:end], current_queries, 0)
            write_end = end if end == total else start + step
            keep = write_end - start
            all_tracks[start:write_end] = chunk_tracks[:keep]
            all_visibs[start:write_end] = chunk_visibs[:keep]
            if end == total:
                break
            next_start = start + step
            current_queries = chunk_tracks[next_start - start].copy()
            start = next_start
        return all_tracks, all_visibs


class Entity3DTracker:
    """Step3-5: 查询深度 → 投影 3D → 更新实体椭球/中心。"""

    def __init__(self, episode, stride: int = 3):
        """episode: YuanliEpisodeRGBD; stride: 检测帧间隔 (默认 3 → 10Hz)。"""
        self.ep = episode
        self.stride = stride
        self._centers: dict[str, np.ndarray] = {}   # eid → 平滑后中心
        self._zref: dict[str, float] = {}           # eid → 参考深度 (锚定, 防 poisoning)
        self._frozen: dict[str, np.ndarray] = {}    # eid → 静态基准中心 (滞回/重入基准)
        self._was_lost: dict[str, bool] = {}         # eid → 上一段是否丢失 (遮挡后重入门控)
        self._occluded: dict[str, bool] = {}         # eid → 当前是否遮挡冻结 (滞回)

    def acknowledge_reground(self, eid: str, c_new: np.ndarray):
        """SAM3 重锚定成功后同步内部状态: 重置 frozen/z_ref/was_lost/occluded。

        CoTracker 在遮挡期会漂移, 重锚定后必须把内部基准刷到 SAM3 给的新中心,
        否则下一帧 CoTracker 与陈旧 frozen 比对会误判为"物体移动了"。
        """
        c = np.asarray(c_new, dtype=np.float64).reshape(3)
        self._frozen[eid] = c.copy()
        self._zref[eid] = float(c[2])
        self._was_lost[eid] = False
        self._occluded[eid] = False
        self._centers[eid] = c.copy()

    def project_uv_to_3d(self, frame_idx: int, uv: np.ndarray,
                         vis: np.ndarray | None = None,
                         z_ref: float | None = None) -> np.ndarray:
        """Step3+4: (u,v) → 深度 → 相机系 3D 点 (剔除不可见/无效/串深度点)。

        小孔模型: X=(u-cx)·Z/fx  Y=(v-cy)·Z/fy  Z=depth(u,v)
        深度一致性: 跟踪点滑到机械臂/背景上时, 深度会跳变 ——
        与实体参考深度 z_ref 偏差超过 max(0.15, 0.2·z_ref) 的点剔除。
        """
        if vis is not None:
            keep = vis > _VIS_TH
            uv, vis = uv[keep], vis[keep]
        if len(uv) == 0:
            return np.zeros((0, 3), np.float64)
        z = self.ep.depth_at(frame_idx, uv).astype(np.float64)
        ok = (z > 0.15) & (z < 3.0)
        if z_ref is not None:
            ok &= np.abs(z - z_ref) < max(0.08, 0.12 * z_ref)
        uv, z = uv[ok], z[ok]
        if len(uv) == 0:
            return np.zeros((0, 3), np.float64)
        fx, fy = self.ep.K[0, 0], self.ep.K[1, 1]
        cx, cy = self.ep.K[0, 2], self.ep.K[1, 2]
        return np.stack([(uv[:, 0] - cx) * z / fx,
                         (uv[:, 1] - cy) * z / fy, z], axis=1)

    def update(self, eid: str, frame_idx: int, tracks_uv: np.ndarray,
                vis: np.ndarray, force_moving: bool = False,
                freeze_on_occlusion: bool = True):
        """Step5: 用 CoTracker 3D 点更新实体中心 (radii/axes 冻结在帧 0)。

        静态先验 ("无证据不动"):
          - 非bonded物体默认静止; 中心只在"证据"下更新, 否则冻结
          - 遮挡冻结 (滞回, 仅 C_obstacle): vis_frac < 0.6 → 冻结 (幸存点偏边缘);
            恢复需 vis_frac > 0.8。A_target 不冻结 (需跟随夹爪操作)。
          - 规则1 静态死区: disp < 2cm 视为噪声, 中心冻结, z_ref 不刷新 (防 poisoning)
          - 规则2 遮挡后重入: 曾丢失时, 仅当物体未动 (disp<5cm) 才恢复 CoTracker;
            物体若在遮挡期移动了 → 拒绝 (要求 SAM3 重锚定, pipeline 端冻结)
          - 规则3 z_ref 锚定: 仅在被接受的更新里刷新, 不逐帧 EMA → 杜绝慢 poisoning
          - force_moving (bonded): 物体被夹持, 视觉直接接受, 跳过静态先验
        """
        result = {"n_pts": 0, "center": None, "lost": False,
                  "accepted": False, "occluded": False}

        # 遮挡冻结 (滞回): 部分 tracker 点不可见时, 幸存点偏向可见边缘 →
        # 中位数偏置 → 椭球移位。仅对 C_obstacle (杯子等静态障碍物) 生效;
        # A_target (teapot/lid/kettle) 需跟随夹爪操作, 不冻结。
        if (freeze_on_occlusion and not force_moving
                and vis is not None and len(vis) > 0):
            vis_frac = float(np.mean(vis > _VIS_TH))
            is_occ = self._occluded.get(eid, False)
            if not is_occ:
                if vis_frac < _OCCLUDE_ENTER:
                    self._occluded[eid] = True
                    result["occluded"] = True
                    return result
            else:
                if vis_frac < _OCCLUDE_EXIT:
                    result["occluded"] = True
                    return result
                self._occluded[eid] = False

        z_ref = self._zref.get(eid)
        pts = self.project_uv_to_3d(frame_idx, tracks_uv, vis, z_ref=z_ref)
        result["n_pts"] = len(pts)
        if len(pts) < 6:
            self._was_lost[eid] = True
            result["lost"] = True
            return result
        c_new = np.median(pts, axis=0)
        z_med = float(np.median(pts[:, 2]))

        # force_moving (bonded): 被夹持物体跟随视觉, 不走静态先验
        if force_moving:
            self._frozen[eid] = c_new.copy()
            self._zref[eid] = z_med
            self._was_lost[eid] = False
            self._centers[eid] = c_new
            result["center"] = c_new
            result["accepted"] = True
            return result

        frozen = self._frozen.get(eid)
        if frozen is None:
            # 首帧: 接受为静态基准
            self._frozen[eid] = c_new.copy()
            self._zref[eid] = z_med
            self._centers[eid] = c_new
            result["center"] = c_new
            result["accepted"] = True
            return result

        disp = float(np.linalg.norm(c_new - frozen))

        # 安全上限: 非bonded单帧跳 >20cm 视为脏数据 (正常物体达不到)
        if disp > 0.20:
            self._was_lost[eid] = True
            result["lost"] = True
            return result

        # 规则2: 遮挡后重入 — 曾丢失时, 仅当物体未动 (disp<5cm) 才恢复 CoTracker
        if self._was_lost.get(eid, False):
            if disp < 0.05:
                self._was_lost[eid] = False
                self._zref[eid] = z_med
                self._centers[eid] = c_new
                result["center"] = c_new
                result["accepted"] = True
            else:
                # 物体在遮挡期间移动了 → CoTracker 不可信, 要求 SAM3 (pipeline 冻结)
                result["lost"] = True
            return result

        # 规则1: 静态死区 — disp<2cm 视为噪声, 中心冻结, z_ref 不刷新
        if disp < 0.02:
            result["center"] = frozen
            self._centers[eid] = frozen
            # z_ref 保持锚定 (防 poisoning)
        else:
            # 接受移动, 更新 frozen + z_ref
            self._frozen[eid] = c_new.copy()
            self._zref[eid] = z_med
            self._centers[eid] = c_new
            result["center"] = c_new
            result["accepted"] = True
        return result
