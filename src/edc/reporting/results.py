"""一次运行写出的 JSON 记录（带版本）：清理不能写进 JSON 的值、写文件、追加记录。"""
import json
import math
import os
from pathlib import Path
import uuid


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "tolist"):
        return clean(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_record(path, record):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(clean(record), ensure_ascii=False, allow_nan=False) + "\n")
