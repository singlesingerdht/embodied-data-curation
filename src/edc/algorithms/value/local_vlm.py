"""价值算法共用的本地 Qwen3-VL 和 SOP 文本读取。

同一进程里模型只加载一次：首帧摆放（物体名翻译）和人手干预用同一份权重，一起跑时不重复加载。
"""
from functools import lru_cache
import json
import re


@lru_cache(maxsize=2)
def load(path):
    import torch
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
    processor = AutoProcessor.from_pretrained(path, local_files_only=True)
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        path, dtype=torch.bfloat16, device_map='auto', local_files_only=True).eval()
    return processor, model


def parse_json(text):
    """回答里的第一个 JSON 对象；没有或解析不了返回 None。"""
    match = re.search(r'\{.*\}', text, re.S)
    try:
        value = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def ask(path, prompt, images=(), max_new_tokens=1024):
    """images 是 [(说明文字, PIL 图)]。返回 (解析出的 JSON 或 None, 原始回答)；格式不对时再问一次。"""
    import torch
    processor, model = load(path)
    content = [part for label, image in images
               for part in ({'type': 'text', 'text': label}, {'type': 'image', 'image': image})]
    for attempt in range(2):
        text = prompt if not attempt else prompt + '\nReturn exactly one complete JSON object and nothing else.'
        inputs = processor.apply_chat_template(
            [{'role': 'user', 'content': content + [{'type': 'text', 'text': text}]}], tokenize=True,
            add_generation_prompt=True, return_dict=True, return_tensors='pt').to(model.device)
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        raw = processor.batch_decode(output[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
        parsed = parse_json(raw)
        if parsed is not None:
            return parsed, raw
    return None, raw


@lru_cache(maxsize=64)
def sop_text(path, topic):
    """MCAP 里某个 std_msgs/String 话题的第一条有效文字（采集 SOP，如 /instruction_data、/diversity_data）；
    'drop' 是占位消息，跳过。没有这个话题或不是 MCAP 返回 None。"""
    if not str(path).endswith('.mcap'):
        return None
    from mcap.reader import make_reader
    from rosbags.typesys import Stores, get_typestore
    store = get_typestore(Stores.ROS2_HUMBLE)
    with open(path, 'rb') as stream:
        for schema, _, message in make_reader(stream).iter_messages(topics=[topic]):
            text = store.deserialize_cdr(message.data, schema.name).data.strip()
            if text and text != 'drop':
                return text
    return None
