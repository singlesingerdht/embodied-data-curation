"""首帧道具位置：SOP 写的物体是否放在指定的桌面九宫格。

1. 物体名：SOP 字段名（如「装有水的杯子在操作面位置」）-> 英文类别名，并标出扁平/大件物体（names.py）
2. 九宫格：Grounding DINO 框出桌子、SAM2 抠出桌面，拟合桌子远边得到两个角；近边在画面外，用 near_y
   （近边在画面里的纵坐标，大于 1 表示在画面下方之外）和 spread（近边宽度是远边的几倍）描述；四个角透视后均分 3×3
3. 物体位置：Grounding DINO 找物体，用框底边中点（物体与桌面接触处）换算到九宫格
4. 判定：任一物体与 SOP 差 2 格及以上，或 2 个及以上物体各差 1 格，就报警。
   扁平/大件物体、同一条数据里英文名重复的物体不判；「正左/正右」按左中/右中算
"""
from collections import Counter
from functools import lru_cache

import cv2
import numpy as np

COLS, ROWS = '左中右', '上中下'
SKIP_KEYS = {'Change', 'Version'}             # diversity 里不是物体的字段
CELL_ALIAS = {'正左': '左中', '正右': '右中'}  # SOP 里的非九宫格写法
MIN_OFF = 2         # 某个物体差几格及以上就报警
MIN_OFF1_COUNT = 2  # 或者：几个及以上物体各差 1 格也报警（多个物体同时偏一格在正常数据里少见）


@lru_cache(maxsize=1)
def models(detector, segmenter, segmenter_config):
    """Grounding DINO（按文字找物体）+ SAM2（抠桌面），同一进程只加载一次。"""
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
    processor = AutoProcessor.from_pretrained(detector, local_files_only=True)
    gdino = AutoModelForZeroShotObjectDetection.from_pretrained(detector, local_files_only=True).to('cuda')
    return processor, gdino, SAM2ImagePredictor(build_sam2(segmenter_config, segmenter, device='cuda'))


def detect(m, img, names, min_score=.3):
    """{英文名: (置信度, [x1, y1, x2, y2])}，坐标是 0~1 比例，每个名字只留最高分的框。"""
    import torch
    processor, gdino, _ = m
    w, h = img.size
    inputs = processor(images=img, text='. '.join(names) + '.', return_tensors='pt').to('cuda')
    with torch.no_grad():
        outputs = gdino(**inputs)
    res = processor.post_process_grounded_object_detection(
        outputs, inputs.input_ids, threshold=min_score, text_threshold=min_score, target_sizes=[(h, w)])[0]
    best = {}
    for box, label, score in zip(res['boxes'].tolist(), res['text_labels'], res['scores'].tolist()):
        if label in names and score > best.get(label, (0,))[0]:
            best[label] = (score, [box[0] / w, box[1] / h, box[2] / w, box[3] / h])
    return best


def table_mask(m, img):
    """桌面区域（True/False 二维数组）；找不到桌子返回 None。"""
    found = detect(m, img, ['table'], min_score=.2)
    if 'table' not in found:
        return None
    w, h = img.size
    x1, y1, x2, y2 = found['table'][1]
    m[2].set_image(np.array(img))
    masks, _, _ = m[2].predict(box=np.array([x1 * w, y1 * h, x2 * w, y2 * h]), multimask_output=False)
    return masks[0].astype(bool)


def far_corners(mask):
    """桌面远边的左右两个角 ((xl, yl), (xr, yr))，0~1 比例。

    每一列取桌面最上面的像素拟合成直线（远边）；被物体挡住的列偏离直线，反复剔除；贴着直线的最左、最右列就是两个角。
    """
    h, w = mask.shape
    cols = np.where(mask.any(axis=0))[0]
    top = mask[:, cols].argmax(axis=0)
    keep = np.ones(len(cols), bool)
    for _ in range(3):
        k, b = np.polyfit(cols[keep], top[keep], 1)
        res = np.abs(top - (k * cols + b))
        keep = res < max(6, np.median(res))  # 离直线 6 像素以内算贴着远边；偏差普遍大时放宽到中位数
    xl, xr = cols[keep].min(), cols[keep].max()
    return (xl / w, (k * xl + b) / h), (xr / w, (k * xr + b) / h)


def make_grid(corners, near_y, spread):
    """「画面 -> 拉正后的桌面」的透视矩阵。"""
    (xl, yl), (xr, yr) = corners
    mid, half = (xl + xr) / 2, (xr - xl) / 2
    src = np.float32([[xl, yl], [xr, yr], [mid + spread * half, near_y], [mid - spread * half, near_y]])
    return cv2.getPerspectiveTransform(src, np.float32([[0, 0], [1, 0], [1, 1], [0, 1]]))


def to_cell(matrix, x, y):
    """画面上一点（0~1 比例）-> 九宫格 (列号, 行号)，各 0/1/2。"""
    u, v = cv2.perspectiveTransform(np.float32([[[x, y]]]), matrix)[0, 0]
    return int(np.clip(u * 3, 0, 2)), int(np.clip(v * 3, 0, 2))


def check(m, img, diversity, names, near_y, spread):
    """返回 (物体结果列表, 是否报警)；找不到桌子返回 (None, False)。names 是 names.lookup 的结果。"""
    div = {k: CELL_ALIAS.get(v, v) for k, v in diversity.items() if k not in SKIP_KEYS}
    objs, skipped = [], []
    for key, cell in div.items():
        n = names.get(key)
        en = n and (n['en'] if isinstance(n['en'], list) else [n['en']])
        reason = ('物体名没有翻译' if not n else '扁平或大件物体' if n['flat'] else '一个字段写了多个物体' if len(en) != 1
                  else 'SOP 格子不是九宫格写法' if len(cell) != 2 or cell[0] not in COLS or cell[1] not in ROWS else '')
        if reason:
            skipped.append({'field': key, 'sop_cell': cell, 'result': '不判', 'reason': reason})
        else:
            objs.append((key, en[0], cell))
    counts = Counter(en for _, en, _ in objs)  # 同名物体分不清谁是谁，不判
    skipped += [{'field': k, 'sop_cell': c, 'result': '不判', 'reason': '和别的物体同名'} for k, en, c in objs if counts[en] > 1]
    objs = [o for o in objs if counts[o[1]] == 1]

    mask = table_mask(m, img)
    if mask is None:
        return None, False
    matrix = make_grid(far_corners(mask), near_y, spread)
    found = detect(m, img, [en for _, en, _ in objs]) if objs else {}
    results = []
    for key, en, cell in objs:
        if en not in found:
            results.append({'field': key, 'name': en, 'sop_cell': cell, 'result': '没找到'})
            continue
        x1, _, x2, y2 = found[en][1]
        col, row = to_cell(matrix, (x1 + x2) / 2, y2)
        off = abs(col - COLS.index(cell[0])) + abs(row - ROWS.index(cell[1]))
        results.append({'field': key, 'name': en, 'sop_cell': cell, 'actual_cell': COLS[col] + ROWS[row],
                        'cells_off': off, 'score': round(found[en][0], 2), 'box': [round(v, 3) for v in found[en][1]],
                        'result': '通过'})
    offs = [r['cells_off'] for r in results if 'cells_off' in r]
    alarm = any(x >= MIN_OFF for x in offs) or sum(x == 1 for x in offs) >= MIN_OFF1_COUNT
    for r in results:
        if alarm and r.get('cells_off', 0) >= 1:
            r['result'] = '报警'
    return results + skipped, alarm
