import numpy as np

class GeometryInput:
    def backproject(self, mask: np.ndarray, frame_idx: int, stride: int=2, valid_z: tuple=(0.15, 3.0)) -> np.ndarray:
        """mask 内像素反投影为相机系 3D 点云 (米)。

            小孔模型: X=(u-cx)Z/fx, Y=(v-cy)Z/fy, Z=depth(u,v)
            剔除 depth=0 (无效) 和超范围深度。
            """
        H, W = mask.shape
        vs, us = np.nonzero(mask)
        vs, us = (vs[::stride], us[::stride])
        z = self.depth[frame_idx][vs, us].astype(np.float64)
        ok = (z > valid_z[0]) & (z < valid_z[1])
        us, vs, z = (us[ok], vs[ok], z[ok])
        fx, fy = (self.K[0, 0], self.K[1, 1])
        cx, cy = (self.K[0, 2], self.K[1, 2])
        x = (us - cx) * z / fx
        y = (vs - cy) * z / fy
        return np.stack([x, y, z], axis=1)

    def depth_at(self, frame_idx: int, uv: np.ndarray, k: int=3) -> np.ndarray:
        """双线性+中值鲁棒查询: 每个点取 kxk 窗口内有效深度的中位数 (米)。
            uv: (N,2) float (u=x, v=y)。返回 (N,) 深度, 0 表示无效。"""
        H, W = self.depth.shape[1:3]
        out = np.zeros(len(uv), np.float32)
        dmap = self.depth[frame_idx]
        u = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
        v = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
        for i in range(len(uv)):
            win = dmap[max(v[i] - k // 2, 0):v[i] + k // 2 + 1, max(u[i] - k // 2, 0):u[i] + k // 2 + 1]
            wv = win[win > 0]
            if len(wv):
                out[i] = float(np.median(wv))
        return out

    def project_to_pixels(self, pts_cam: np.ndarray) -> np.ndarray:
        """3D 相机系点 → 像素 (u,v)。透视除法: u = fx X/Z + cx。"""
        p = np.asarray(pts_cam, np.float64).reshape(-1, 3)
        z = np.clip(p[:, 2], 1e-06, None)
        u = self.K[0, 0] * p[:, 0] / z + self.K[0, 2]
        v = self.K[1, 1] * p[:, 1] / z + self.K[1, 2]
        return np.stack([u, v], axis=1)
