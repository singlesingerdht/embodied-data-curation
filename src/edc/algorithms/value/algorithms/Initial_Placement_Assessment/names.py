"""SOP 物体字段名 -> 英文类别名 + 是否扁平/大件。

先查 object_names.json；新字段名问本地 Qwen3-VL，答上的写回这个文件。文件可以人工修改，改了以后以文件为准。
"""
import json
import os
from pathlib import Path

CACHE = Path(__file__).with_name('object_names.json')
BATCH = 20  # 每次问几个字段名

PROMPT = """下面是机器人数据采集 SOP 里描述物体摆放位置的字段名，每行一个：
{keys}

对每个字段名，给出：
1. en：字段里物体的英文名，用于目标检测。用最常见的物体类别词（1~2 个单词，如 bowl、cup、spoon、jar、box、bottle），不要写材质、内容物或用途（如“带盖烘焙面粉罐”写 jar，“空纸质小盒”写 box）。字段里有多个物体时写成英文名列表。
2. flat：这个物体是不是扁平或体积很大、中心位置难以确定的物体（如布、托盘、盘子、湿纸巾、电线、衣架、扫把），是写 true，否则写 false。

只输出一个 JSON 对象，键是原字段名，不要别的文字，格式：
{{"字段名": {{"en": "jar", "flat": false}}}}"""


def lookup(keys, model=None):
    """{字段名: {'en': 英文名或列表, 'flat': bool}}；没有缓存又没给模型、或模型没答上的字段不在结果里。"""
    cache = json.loads(CACHE.read_text(encoding='utf-8'))
    missing = [k for k in dict.fromkeys(keys) if k not in cache]
    if missing and model:
        from edc.algorithms.value.local_vlm import ask
        for i in range(0, len(missing), BATCH):
            batch = missing[i:i + BATCH]
            answer, _ = ask(model, PROMPT.format(keys='\n'.join(batch)), max_new_tokens=2048)
            for k in batch:
                v = (answer or {}).get(k)
                if isinstance(v, dict) and v.get('en'):
                    cache[k] = {'en': v['en'], 'flat': bool(v.get('flat'))}
        temporary = CACHE.with_suffix(f'.{os.getpid()}.tmp')  # 先写临时文件再改名，几个进程同时写也不会写坏
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
        temporary.replace(CACHE)
    return {k: cache[k] for k in keys if k in cache}
