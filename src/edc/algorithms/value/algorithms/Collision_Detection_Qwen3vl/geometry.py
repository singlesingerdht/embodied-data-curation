"""几何核心: 椭球(MVEE)/平面(RANSAC)/三大碰撞触发数学.

全部基于 numpy, 无 GPU 依赖。单位: 米(m)、相机光学坐标系 (x右, y下, z前)。

核心数学速查:
  椭球: {x | (x-c)^T Q (x-c) <= 1}, Q = R diag(1/r_i^2) R^T (正定)
  支撑函数: h(n) = sqrt(n^T A n) / ||n||, A = Q^{-1} = R diag(r_i^2) R^T
            —— 椭球沿方向 n 的最大伸展半幅, 碰撞检测的关键
"""
from __future__ import annotations

import numpy as np


# ─────────────────────────── 椭球 ───────────────────────────

class Ellipsoid:
    """最小体积包围椭球 (MVEE)。

    属性:
        center: (3,) 质心
        axes:   (3,3) 列向量 = 椭球三个主轴方向 (单位正交)
        radii:  (3,)   沿三个主轴的半轴长
    """

    def __init__(self, center: np.ndarray, axes: np.ndarray, radii: np.ndarray,
                 padding: float = 1.05):
        self.center = np.asarray(center, dtype=np.float64).reshape(3)
        # axes 列向量 = 主轴方向。eigh 已保证正交, 不可再 QR (会破坏 轴-半径 配对)
        self.axes = np.asarray(axes, dtype=np.float64).reshape(3, 3)
        # padding: 轻微膨胀, 吸收深度噪声/点云稀疏
        self.radii = np.asarray(radii, dtype=np.float64).reshape(3) * padding
        self.volume = 4.0 / 3.0 * np.pi * float(np.prod(self.radii))

    # —— 形状矩阵 ——
    @property
    def A(self) -> np.ndarray:
        """A = R diag(r^2) R^T —— 支撑函数用。"""
        return self.axes @ np.diag(self.radii ** 2) @ self.axes.T

    @property
    def Q(self) -> np.ndarray:
        """Q = A^{-1} = R diag(1/r^2) R^T —— 二次型判定用。"""
        return self.axes @ np.diag(1.0 / self.radii ** 2) @ self.axes.T

    def support(self, n: np.ndarray) -> float:
        """支撑函数 h(n) = sqrt(n^T A n)/||n||: 沿方向 n 的椭球半幅。"""
        n = np.asarray(n, dtype=np.float64).reshape(3)
        return float(np.sqrt(n @ self.A @ n) / np.linalg.norm(n))

    def contains(self, p: np.ndarray) -> bool:
        """点是否在椭球内: (p-c)^T Q (p-c) <= 1。"""
        d = np.asarray(p, dtype=np.float64).reshape(3) - self.center
        return float(d @ self.Q @ d) <= 1.0

    def scale_axis(self, axis_idx: int, factor: float) -> "Ellipsoid":
        """沿第 axis_idx 主轴缩放 radii (夹爪'呼吸'用)。"""
        radii = self.radii.copy()
        radii[axis_idx] *= factor
        return Ellipsoid(self.center, self.axes, radii, padding=1.0)

    def to_dict(self) -> dict:
        return {
            "center": self.center.tolist(),
            "axes": self.axes.tolist(),
            "radii": self.radii.tolist(),
            "volume": self.volume,
        }


def fit_mvee(points: np.ndarray, tol: float = 1e-6, max_iter: int = 500) -> Ellipsoid:
    """Khachiyan 迭代求最小体积包围椭球 (MVEE)。

    标准算法: Q = [pts^T; 1] ∈ R^{(d+1)×N}, 权重 u∈Δ^N。
    迭代求 max log det(Σ u_i q_i q_i^T) 的对偶 —— 每步把权重从支撑最远的
    点 q_j (马氏距离最大) 转移 step = (m_j - d - 1)/((d+1)(m_j - 1))。
    收敛后: c = Σ u_i p_i,  inv(A) = d·(Σ u_i p_i p_i^T - c c^T),
    椭球 = {x | (x-c)^T A (x-c) <= 1}, 特征值开方即 radii。

    Args:
        points: (N,3) 点云, 建议 N <= 500 (先 voxel 下采样)
        tol:    收敛容限 (权重步长)
    """
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 4:
        raise ValueError(f"MVEE 需要至少 4 个点, got {len(pts)}")
    # 共面防御: 最小奇异方向加微厚度
    centered = pts - pts.mean(0)
    if np.linalg.matrix_rank(centered, tol=1e-8) < 3:
        jitter = (np.abs(centered).max() + 1e-3) * 1e-3
        pts = pts + np.random.RandomState(0).normal(0, jitter, pts.shape)

    d, n = 3, len(pts)
    Q = np.vstack([pts.T, np.ones((1, n))])          # (4, N) 齐次坐标
    u = np.full(n, 1.0 / n)

    for _ in range(max_iter):
        X = Q @ np.diag(u) @ Q.T                     # (4,4)
        Xinv = np.linalg.inv(X)
        m = np.einsum("ij,jk,ki->i", Q.T, Xinv, Q)   # (N,) diag(Q^T X⁻¹ Q)
        j = int(np.argmax(m))
        step = (m[j] - d - 1.0) / ((d + 1.0) * (m[j] - 1.0))
        if abs(step) < tol:
            break
        u *= 1 - step
        u[j] += step

    # 重建椭球: inv(A) = d (Σ u p p^T - c c^T)
    c = u @ pts
    M = (pts - c).T @ np.diag(u) @ (pts - c) * d      # = inv(A)
    evals, evecs = np.linalg.eigh(M)
    evals = np.clip(evals, 1e-9, None)
    return Ellipsoid(c, evecs, np.sqrt(evals), padding=1.0)


def fit_mvee_fast(points: np.ndarray, margin: float = 1.03) -> Ellipsoid:
    """凸包 + Khachiyan MVEE —— 点云大时用。

    MVEE 只由点云的凸包顶点 (极值点) 决定, 内部点不影响结果,
    故先取 ConvexHull 顶点再跑 Khachiyan, 又快又紧。最后按全体
    点的最大马氏距离确认包住 (留 margin 余量)。

    (早期 PCA 3σ 版本只覆盖 ±1.7σ, 漏物体边缘; 随机下采样版本
    丢角点导致校验放大过头 —— 凸包版两者都修掉。)"""
    pts = np.asarray(points, dtype=np.float64)
    core = pts
    if len(pts) > 200:
        try:
            from scipy.spatial import ConvexHull
            hull = ConvexHull(pts)
            core = pts[hull.vertices]
        except Exception:
            core = pts[:: max(1, len(pts) // 200)]
    ell = fit_mvee(core)
    # 包含性校验: 全体点的 Mahalanobis 距离, 取最大者放大 radii
    local = (pts - ell.center) @ ell.axes            # 椭球局部坐标 (axes 列向量=主轴)
    maha = np.sqrt(((local / ell.radii) ** 2).sum(1))  # Mahalanobis (N,)
    scale = max(float(maha.max()), 1.0) * margin
    return Ellipsoid(ell.center, ell.axes, ell.radii * scale, padding=1.0)


def fit_mvee_robust(points: np.ndarray, trim_pct: float = 99.0,
                    axis_cap: float = 1.25, margin: float = 1.05) -> Ellipsoid:
    """鲁棒 MVEE: 离群剔除 + 凸包拟合 + 半轴封顶 + 包封校验。

    掩码偶尔抽风 (SAM3 选到更差实例/掺入背景深度尖刺) 时, 裸 MVEE
    会被极端离群点拉爆 (半轴远超点云本身跨度)。防御链:
      1. 按到中位点距离的 trim_pct 分位剔除离群点
      2. 凸包顶点 Khachiyan MVEE
      3. 每根半轴不得超过 axis_cap × 点云沿该轴最大半展 (包封椭球
         合法范围的上界)
      4. 全体点 Mahalanobis 校验, 不足则均匀放大到包住
    """
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 4:
        raise ValueError(f"MVEE 需要至少 4 个点, got {len(pts)}")
    # 1) 离群剔除 (深度尖刺/掩码杂点)
    if len(pts) > 100:
        med = np.median(pts, axis=0)
        dist = np.linalg.norm(pts - med, axis=1)
        keep = dist <= np.percentile(dist, trim_pct)
        if keep.sum() >= 50:
            pts = pts[keep]
    ell = fit_mvee_fast(pts, margin=1.0)
    # 3) 半轴封顶: 沿椭球各主轴的点云最大半展
    local = (pts - ell.center) @ ell.axes
    half_extent = np.abs(local).max(0)
    capped = np.minimum(ell.radii, half_extent * axis_cap)
    capped = np.maximum(capped, 5e-3)
    # 4) 全体点包封校验
    maha = np.sqrt(((local / capped) ** 2).sum(1))
    scale = max(float(maha.max()), 1.0) * margin
    return Ellipsoid(ell.center, ell.axes, capped * scale, padding=1.0)


def fit_extent_ellipsoid(points: np.ndarray, trim_pct: float = 99.0,
                         margin: float = 1.10) -> Ellipsoid:
    """PCA 轴半展椭球: 半轴 = 点云沿各 PCA 轴最大投影 × margin。

    被操作目标 (纸巾/盖子等薄物) 专用。MVEE 对盒状点云沿主轴方向
    会鼓包最多 √3×, 导致相邻物体椭球虚假相交; 半展椭球贴合点云
    实际展幅, 分离性优先 (角点少量欠覆盖可接受)。"""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 4:
        raise ValueError(f"椭球拟合需要至少 4 个点, got {len(pts)}")
    # 离群剔除 (深度尖刺/掩码杂点)
    if len(pts) > 100:
        med0 = np.median(pts, axis=0)
        dist = np.linalg.norm(pts - med0, axis=1)
        keep = dist <= np.percentile(dist, trim_pct)
        if keep.sum() >= 50:
            pts = pts[keep]
    c = np.median(pts, axis=0)          # 中位数中心, 抗偏态
    local = pts - c
    _, evecs = np.linalg.eigh(np.cov(local.T))  # eigh 按特征值升序
    axes = evecs[:, ::-1]               # 翻转成降序: 长轴在前
    # p98 半展 (非 max): max 对掩码边缘/盒缘离群点极敏感, 把薄板的
    # 法向半轴从 ~2cm 撑到 8cm+ → 椭球体积虚胖 4 倍 + 相邻椭球假相交
    proj = np.abs(local @ axes)
    radii = np.percentile(proj, 98, axis=0) * margin
    radii = np.maximum(radii, 1.5e-2)   # 最小半径 1.5cm 防深度噪声退化成薄片
    return Ellipsoid(c, axes, radii, padding=1.0)


# ─────────────────────────── 平面 ───────────────────────────

class Plane:
    """3D 平面 Ax+By+Cz+D=0, 法线 n=(A,B,C) 规范化为单位向量、朝向相机(负 z)。"""

    def __init__(self, params: np.ndarray, inliers: int = -1, rmse: float = -1.0):
        n = np.asarray(params[:3], dtype=np.float64)
        norm = np.linalg.norm(n)
        params = params / norm  # 归一化使 ||n||=1
        n, d = params[:3], params[3]
        if n[2] > 0:  # 相机光学系 z 朝前, 桌面法线应朝相机 (负 z 分量)
            n, d = -n, -d
        self.n = n
        self.d = float(d)
        self.inliers = inliers
        self.rmse = rmse

    def signed_distance(self, p: np.ndarray) -> float:
        """点 到平面的带符号距离 (n·p+D, ||n||=1)。
        相机一侧 (z 小) 为正。"""
        p = np.asarray(p, dtype=np.float64).reshape(3)
        return float(self.n @ p + self.d)

    def to_dict(self) -> dict:
        return {"normal": self.n.tolist(), "d": self.d,
                "inliers": self.inliers, "rmse": self.rmse}


def fit_plane_ransac(points: np.ndarray, dist_thresh: float = 0.008,
                     iters: int = 300) -> Plane:
    """RANSAC 拟合主平面 (桌面/墙)。open3d 不必要时用纯 numpy 实现。"""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 50:
        raise ValueError("平面拟合需要 >=50 点")
    try:
        import open3d as o3d
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pts)
        params, inlier_idx = pcd.segment_plane(dist_thresh, 3, iters)
        in_pts = pts[inlier_idx]
        # 最小二乘精化
        c = in_pts.mean(0)
        # Only the three right singular vectors are needed for the plane normal.
        # A full U allocates N x N elements for a dense scene point cloud.
        _, _, vt = np.linalg.svd(in_pts - c, full_matrices=False)
        n = vt[-1]
        d = -n @ c
        return Plane(np.append(n, d), inliers=len(inlier_idx),
                     rmse=float(np.std((in_pts - c) @ n)))
    except ImportError:
        # 纯 numpy RANSAC 兜底
        rng = np.random.RandomState(42)
        best_in, best_model = 0, None
        for _ in range(iters):
            idx = rng.choice(len(pts), 3, replace=False)
            p0, p1, p2 = pts[idx]
            n = np.cross(p1 - p0, p2 - p0)
            nn = np.linalg.norm(n)
            if nn < 1e-9:
                continue
            n /= nn
            d = -n @ p0
            inl = np.abs(pts @ n + d) < dist_thresh
            if inl.sum() > best_in:
                best_in, best_model = int(inl.sum()), np.append(n, d)
        return Plane(best_model, inliers=best_in)


# ─────────────────────── 三大碰撞触发数学 ───────────────────────

def trigger_penetration(ell: Ellipsoid, plane: Plane, margin: float = 0.01) -> dict:
    """触发器1: 平面穿透。

    数学: 椭球中心到平面带符号距离 d_s = n·c + D (相机侧为正)。
          椭球沿法线 n 的半幅 h = sqrt(n^T A n) (单位法线)。
          触发: d_s < h - margin  即椭球表面越过平面 (margin 吸收深度噪声)。
    """
    d_s = plane.signed_distance(ell.center)
    h = ell.support(plane.n)
    depth = h - d_s  # >0 表示穿透深度
    return {
        "triggered": bool(depth > margin),
        "signed_distance": d_s,
        "half_extent": h,
        "penetration_depth": float(max(0.0, depth)),
    }


def trigger_intersection(e1: Ellipsoid, e2: Ellipsoid,
                         shrink: float = 0.0) -> bool:
    """触发器2: 椭球-椭球相交 (分离轴定理 SAT 近似)。

    数学: 对候选轴 a (取两椭球各自 3 条主轴 + 连心线), 若存在
          |a·(c1-c2)| > sqrt(a^T A1 a) + sqrt(a^T A2 a)  (||a||=1)
          则两凸体必分离; 全部轴都无法分离 → 判相交。
          注意: 椭球非多面体, SAT 只是必要条件的近似 (保守: 可能少量误报),
          工程上以主轴+连心线 7 条轴已足够稳定。
    shrink: 收缩因子 (0~1), 将两椭球半径同比缩小, 用于降低保守误报。
    """
    if shrink > 0:
        e1 = Ellipsoid(e1.center, e1.axes, e1.radii * (1 - shrink), padding=1.0)
        e2 = Ellipsoid(e2.center, e2.axes, e2.radii * (1 - shrink), padding=1.0)
    c = e1.center - e2.center
    if np.linalg.norm(c) < 1e-12:
        return True
    axes = [e1.axes[:, i] for i in range(3)] + [e2.axes[:, i] for i in range(3)]
    axes.append(c / np.linalg.norm(c))  # 连心线
    for a in axes:
        gap = abs(a @ c) - (e1.support(a) + e2.support(a))
        if gap > 0:
            return False  # 找到分离轴
    return True


def trigger_displacement(c_init: np.ndarray, c_now: np.ndarray,
                         thresh: float = 0.02) -> dict:
    """触发器3: 被动位移 —— 障碍物中心漂移超过阈值 (默认 2cm)。
    物理含义: 障碍物被碰倒/推走, 间接碰撞证据。"""
    d = float(np.linalg.norm(np.asarray(c_now) - np.asarray(c_init)))
    return {"triggered": bool(d > thresh), "displacement": d, "threshold": thresh}
