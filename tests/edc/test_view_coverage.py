"""拍摄视角覆盖：合成画面上找镜头污渍。"""
import numpy as np

from edc.algorithms.quality.algorithms.View_Coverage.method import find_smudge


def frames(blob):
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[:240, :320]
    shade = 1 - .35 * np.exp(-((xx - 160) ** 2 + (yy - 120) ** 2) / (2 * 6 ** 2)) if blob else 1
    out = []
    for _ in range(30):  # 每帧亮度不同、带噪声，污渍位置不变
        base = rng.uniform(150, 200) + rng.normal(0, 2, (240, 320))
        out.append(np.repeat((base * shade)[..., None], 3, 2).clip(0, 255).astype(np.uint8))
    return np.stack(out)


def test_soft_round_blob_is_found_at_its_position():
    dark, spot = find_smudge(frames(True))
    assert dark >= .2 and abs(spot['x'] - .5) < .05 and abs(spot['y'] - .5) < .05


def test_clean_lens_is_not_flagged():
    assert find_smudge(frames(False))[0] < .2
