"""Dense scene fitting must not allocate a quadratic-size left singular matrix."""
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.geometry import fit_plane_ransac


def test_open3d_plane_refinement_preserves_normal_without_square_workspace(monkeypatch):
    rng=np.random.RandomState(42)
    points=rng.uniform(-1,1,(1000,3))
    points[:,2]=.2*points[:,0]-.3*points[:,1]+1+rng.normal(0,1e-5,1000)
    class Cloud:
        def segment_plane(self,*args):return [0,0,1,-1],list(range(len(points)))
    monkeypatch.setitem(sys.modules,'open3d',NS(
        geometry=NS(PointCloud=Cloud),utility=NS(Vector3dVector=lambda value:value)))
    svd=np.linalg.svd
    center=points.mean(0)
    expected=svd(points-center,full_matrices=False)[2][-1]
    calls=[]
    def reduced(values,**kwargs):
        assert kwargs.get('full_matrices') is False
        calls.append(values.shape)
        return svd(values,**kwargs)
    monkeypatch.setattr(np.linalg,'svd',reduced)
    plane=fit_plane_ransac(points)
    assert calls==[(1000,3)]
    assert abs(np.dot(plane.n,expected))==pytest.approx(1)
    assert abs(plane.signed_distance(center))<1e-10
    assert plane.inliers==1000
