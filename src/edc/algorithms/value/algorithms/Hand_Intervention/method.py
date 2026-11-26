"""人手干预：多路相机里有没有人的手/手臂新进入任务区，或碰到、移动任务物体。

1. 扫描：每 window_seconds 秒一个窗口（相邻窗口重叠 2 秒），每路相机按 scan_fps 抽帧拼成一张带时间的拼图，
   连同任务说明和上一窗口末的人手状态一起问 Qwen3-VL，宁多勿漏地列出候选
2. 合并：间隔 2 秒以内的候选并成一段，合并后最长 14 秒
3. 复核：每段前后各加 1 秒，按 review_fps 抽更密的帧再问一次，结论为确认 / 需复核 / 排除。
   确认必须写出看得见的人手接触或移动物体；只看到进入、靠近的降为需复核
4. 整条结论：有确认 -> FAIL；有需复核（含扫描失败的窗口）-> NEEDS_REVIEW；否则 PASS
本方法取自 Collision_Detection_Qwen3vl（v62）的人手部分，去掉了碰撞、洒漏、盖子跌落和几何输入。
"""
import json

import cv2
import numpy as np

OVERLAP, MERGE_GAP, MAX_EVENT, PAD = 2.0, 2.0, 14.0, 1.0  # 秒
TILE_HEIGHT, COLS, MAX_TILES = 180, 4, 32  # 每格高 180 像素；一张拼图每行 4 格、最多 32 格

SCAN_PROMPT = """You are a policy-blind multi-view observer of a teleoperated robot task.
Task (context only, not evidence): {task}
Views: {views}. Each image is a chronological contact sheet of one view; every tile is stamped with its time in seconds.
Interval: {start:.1f}-{end:.1f}s. Human hand state at the end of the previous interval: {state}

The robot's own arms and grippers are NOT human hands.
Generate high-recall candidates of exactly one type:
hand_intervention: a new human hand/arm entry into the task workspace, direct touch, or movement of a task object by
a human. Background-only presence and continued presence are state, not new events.
Return JSON only:
{{"hand":"absent|present|unknown","candidates":[{{"start":0.0,"end":0.0,"objects":[],"views":[],"confidence":0.0,
"observation":{{"before":"","transition":"","after":"","visibility":"clear|partial|occluded"}}}}]}}"""

REVIEW_PROMPT = """You are the final judge of human hand intervention in a teleoperated robot task.
Task (context only, not evidence): {task}
Candidate from a coarse scan (not evidence): {candidate}
Attached: one chronological contact sheet per view ({views}) covering {start:.1f}-{end:.1f}s at {fps:g} frames per
second; every tile is stamped with its time in seconds. The robot's arms and grippers are NOT human hands.

Decide one verdict:
- confirmed: a human hand/arm is in the task workspace and visibly touches or moves a task object.
- needs_review: a human hand/arm enters the task workspace but contact is not clearly visible, or occlusion prevents a decision.
- rejected: no human hand/arm in the workspace, background presence only, or the "hand" is the robot gripper.
In objects, list ONLY objects whose boundary is visibly touched or whose motion begins with the hand.
Return JSON only:
{{"verdict":"confirmed|needs_review|rejected","confidence":0.0,"objects":[],"evidence_chain":[],"limitations":[]}}
Evidence-chain entries must be concrete observations naming a view, a time, the hand and the object."""

ACTIONS = ('touch', 'contact', 'grasp', 'grip', 'hold', 'pick', 'lift', 'reposition', 'set down', 'place', 'remove',
           'manipulat', 'push', 'pull', 'displac', 'mov', '接触', '触碰', '抓', '拿', '抬', '移动', '摆放', '放置',
           '取下', '推', '拉')
NEGATIONS = ('no ', 'not ', 'without ', 'never ', 'cannot ', "n't ", '未', '没有', '无')


def sample(episode, views, fps):
    """{视角: [(相对秒, 缩小后的 RGB 图)]}，每路按 fps 抽帧；时间从各路最早的一帧算起。"""
    raw = {}
    for label, name in views.items():
        rows, first, last = [], None, -1
        for frame in episode.iter_frames(name):
            t = frame['source_time']
            first = t if first is None else first
            if int((t - first) * fps) > last:
                last = int((t - first) * fps)
                h, w = frame['image'].shape[:2]
                rows.append((t, cv2.resize(frame['image'], (round(w * TILE_HEIGHT / h), TILE_HEIGHT),
                                           interpolation=cv2.INTER_AREA)))
        raw[label] = rows
    t0 = min(rows[0][0] for rows in raw.values())
    return {label: [(t - t0, image) for t, image in rows] for label, rows in raw.items()}


def contact_sheet(rows, start, end, fps):
    """start~end 秒内按 fps 取帧（最多 MAX_TILES 张）拼成一张图，每格标时间；没有帧返回 None。"""
    from PIL import Image
    picked, last = [], None
    for t, image in rows:
        if start <= t <= end and (last is None or t - last >= 1 / fps - 1e-3):
            picked.append((t, image))
            last = t
    if not picked:
        return None
    picked = [picked[i] for i in np.linspace(0, len(picked) - 1, min(len(picked), MAX_TILES)).round().astype(int)]
    tiles = []
    for t, image in picked:
        tile = np.ascontiguousarray(image)
        cv2.putText(tile, f'{t:.1f}s', (6, 20), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 0), 2, cv2.LINE_AA)
        tiles.append(tile)
    tiles += [np.zeros_like(tiles[0])] * (-len(tiles) % COLS)
    return Image.fromarray(np.vstack([np.hstack(tiles[i:i + COLS]) for i in range(0, len(tiles), COLS)]))


def sheets(frames, start, end, fps):
    return [(f'{label} view {start:.1f}-{end:.1f}s', s) for label, rows in frames.items()
            if (s := contact_sheet(rows, start, end, fps)) is not None]


def background_only(item):
    """扫描结果自己写明只在背景、没有接触的，不算候选。"""
    text = json.dumps(item.get('observation') or {}, ensure_ascii=False).lower()
    return (any(x in text for x in ('background', 'outside the', 'frame edge'))
            and any(x in text for x in ('no contact', 'without touching', 'no approach', '未接触', '未靠近')))


def visible_action(chain):
    """证据里有没有不带否定词的「接触 / 移动物体」描述。"""
    text = ' '.join(str(x) for x in chain or []).lower()
    for word in ACTIONS:
        index = text.find(word)
        while index >= 0:
            if not any(n in text[max(0, index - 48):index] for n in NEGATIONS):
                return True
            index = text.find(word, index + len(word))
    return False


def scan(ask, frames, task, window, fps):
    """返回 (候选列表, 扫描失败的窗口)。ask(prompt, images) -> (JSON 或 None, 原始回答)。"""
    duration = min(rows[-1][0] for rows in frames.values())
    candidates, failures, state, start = [], [], 'unknown', 0.0
    while start < duration:
        end = min(duration, start + window)
        answer, raw = ask(SCAN_PROMPT.format(task=task, views=', '.join(frames), start=start, end=end, state=state),
                          sheets(frames, start, end, fps))
        if answer is None:
            failures.append({'start': start, 'end': end, 'raw': raw[-500:]})
        else:
            state = answer.get('hand', state)
            for item in answer.get('candidates') or []:
                try:
                    s = min(end, max(start, float(item.get('start', start))))
                    e = min(end, max(s, float(item.get('end', s))))
                except (TypeError, ValueError):
                    s, e = start, end
                if not background_only(item):
                    candidates.append({'start': s, 'end': e, 'scan': item})
        if end >= duration:
            break
        start = end - OVERLAP
    return candidates, failures


def merge(candidates):
    """间隔 MERGE_GAP 秒以内的候选并成一段，合并后不超过 MAX_EVENT 秒。"""
    merged = []
    for c in sorted(candidates, key=lambda c: c['start']):
        last = merged[-1] if merged else None
        if last and c['start'] <= last['end'] + MERGE_GAP and max(last['end'], c['end']) - last['start'] <= MAX_EVENT:
            last['end'] = max(last['end'], c['end'])
            last['scans'].append(c['scan'])
        else:
            merged.append({'start': c['start'], 'end': c['end'], 'scans': [c['scan']]})
    return merged


def review(ask, frames, task, event, fps):
    """复核一段候选，返回带 verdict 的事件。"""
    start, end = max(0.0, event['start'] - PAD), event['end'] + PAD
    images = sheets(frames, start, end, fps)
    answer, raw = ask(REVIEW_PROMPT.format(task=task, candidate=json.dumps(event['scans'], ensure_ascii=False),
                                           views=', '.join(frames), start=start, end=end, fps=fps), images)
    answer = answer or {}
    verdict = str(answer.get('verdict', '')).lower()
    chain = [str(x) for x in answer.get('evidence_chain') or []]
    notes = [str(x) for x in answer.get('limitations') or []]
    if verdict not in {'confirmed', 'needs_review', 'rejected'}:
        verdict, notes = 'needs_review', notes + ['复核没有给出有效结论']
    elif verdict != 'needs_review' and not chain:
        verdict, notes = 'needs_review', notes + ['复核没有写出具体的画面证据']
    if verdict == 'confirmed' and not visible_action(chain):
        verdict, notes = 'needs_review', notes + ['只看到人手进入或靠近，没写出看得见的接触或移动物体']
    return {'start': round(event['start'], 2), 'end': round(event['end'], 2), 'verdict': verdict,
            'objects': answer.get('objects') or [] if verdict == 'confirmed' else [],
            'evidence_chain': chain, 'limitations': notes, 'scan_candidates': event['scans'],
            'review_raw': raw[-2000:], 'images': images}
