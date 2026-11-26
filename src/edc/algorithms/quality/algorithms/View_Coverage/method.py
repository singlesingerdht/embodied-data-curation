"""拍摄视角覆盖（第一版：镜头污渍）。

污渍粘在镜头上，有三个特点，在均匀抽出的若干帧上逐像素检查：
    1. 每帧都在：同一位置 90% 以上的帧都比周围暗
    2. 中间浓、往外变淡、边缘发虚：由内到外暗度逐圈下降，附近最陡的边缘不超过 MAX_SHARP（笔、洞、黑色物体边缘清楚）
    3. 是圆的一团：往外 RADIUS 像素的 8 个方向都明显变淡（桌子边缘的阴影是长条，会被排除）
满足三点、且中心比周围暗 min_dark 以上就报。
"""
import cv2
import numpy as np

WIDTH = 320         # 抽出的帧缩到这个宽度
MIN_PERSIST = 0.9   # 至少多少比例的帧这里偏暗
MAX_SHARP = 9       # 附近最陡的边不能超过这个值（越小越虚）；笔、黑色物体一般 10 以上
RADIUS = 10         # 往外走多少像素检查是否变淡
BORDER = 12         # 画面边缘不判（鱼眼边缘本来就暗）


def box(m, k):
    """以每个像素为中心、半径 k 的方框平均。"""
    return cv2.blur(m, (2 * k + 1, 2 * k + 1))


def find_smudge(frames):
    """frames: (N, 高, 宽, 3) RGB。返回 (中心暗度, 位置信息)；没找到返回 (0.0, None)。"""
    darks, grads = [], []
    for im in frames:
        g = cv2.cvtColor(im, cv2.COLOR_RGB2GRAY).astype(np.float32)
        small = cv2.GaussianBlur(g, (0, 0), 1.5)
        big = cv2.GaussianBlur(g, (0, 0), 15)
        darks.append((big - small) / (big + 10))   # 比周围暗多少（相对亮度）
        gm = np.hypot(cv2.Sobel(small, cv2.CV_32F, 1, 0), cv2.Sobel(small, cv2.CV_32F, 0, 1)) / 8
        grads.append(cv2.dilate(gm, np.ones((21, 21), np.uint8)))  # 附近 10 像素内最陡的边
    darks, grads = np.array(darks), np.array(grads)
    md = np.median(darks, 0)
    p3, p6, p10, p15 = (box(md, k) for k in (3, 6, 10, 15))
    persist = (np.array([box(d, 3) for d in darks]) > 0.05).mean(0)
    sharp = np.median(grads, 0)

    ok = (persist > MIN_PERSIST) & (sharp < MAX_SHARP)                        # 特点 1 + 边缘发虚
    ok &= (p3 > p6) & (p6 > p10) & (p10 > p15) & (p15 < 0.5 * p3)           # 特点 2：由内到外变淡
    sm = box(md, 2)
    around = [np.roll(sm, (int(round(RADIUS * np.sin(a))), int(round(RADIUS * np.cos(a)))), (0, 1))
              for a in np.arange(8) * np.pi / 4]
    ok &= np.max(around, 0) < 0.5 * sm                                       # 特点 3：8 个方向都变淡
    ok[:BORDER] = ok[-BORDER:] = False
    ok[:, :BORDER] = ok[:, -BORDER:] = False
    if not ok.any():
        return 0.0, None
    score = np.where(ok, p3, 0)
    y, x = np.unravel_index(score.argmax(), score.shape)
    h, w = md.shape
    return float(score[y, x]), {'x': round(x / w, 3), 'y': round(y / h, 3), 'dark': round(float(p3[y, x]), 3),
                                'sharpness': round(float(sharp[y, x]), 1)}


def sample(episode, name, n):
    """均匀抽 n 帧，缩到 WIDTH 宽，返回 (n, 高, 宽, 3)。视频要从头按顺序解码，所以整段解一遍只留要的帧。"""
    want = set(np.linspace(0, episode.channels[name].count - 1, n).astype(int).tolist())
    frames = []
    for frame in episode.iter_frames(name):
        if frame['index'] in want:
            h, w = frame['image'].shape[:2]
            frames.append(cv2.resize(frame['image'], (WIDTH, round(h * WIDTH / w)), interpolation=cv2.INTER_AREA))
    return np.stack(frames)


def evidence(frames, spot, path):
    """开头、中间、结尾 3 帧并排，红框标出污渍位置。"""
    h, w = frames.shape[1:3]
    x, y, r = int(spot['x'] * w), int(spot['y'] * h), 20
    row = []
    for k in (0, len(frames) // 2, len(frames) - 1):
        im = np.ascontiguousarray(frames[k])
        cv2.rectangle(im, (x - r, y - r), (x + r, y + r), (255, 0, 0), 1)
        row.append(im)
    cv2.imwrite(str(path), cv2.cvtColor(np.concatenate(row, 1), cv2.COLOR_RGB2BGR))


def assess(episode, name, n, min_dark, workdir=None):
    """一路相机的报告（质量框架的原生格式）。"""
    frames = sample(episode, name, n)
    dark, spot = find_smudge(frames)
    found = dark >= min_dark
    report = {'input_channel': name, 'verdict': 'NEEDS_REVIEW' if found else 'ACCEPTABLE', 'passed': not found,
              'score': round(dark, 3), 'assessment_complete': True, 'sampled_frames': len(frames), 'smudge': spot,
              'reasons': [f"疑似镜头污渍：画面 ({spot['x']:.2f}, {spot['y']:.2f}) 处每帧都有一团边缘发虚的暗斑，"
                          f"比周围暗 {dark:.0%}"] if found else []}
    if found and workdir is not None:
        path = workdir / f"smudge-{name.strip('/').replace('/', '_')}.jpg"
        evidence(frames, spot, path)
        report['evidence'] = path.name
    return report
