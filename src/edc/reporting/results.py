"""Versioned records and relocatable local reports. No cross-algorithm score."""
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


def report(output):
    output = Path(output).resolve()
    catalog = json.loads((output / "catalog.json").read_text())
    if catalog.get("schema_version") != "dqs_catalog_v1":
        raise ValueError("Unsupported catalog; legacy results use their original report tools")
    from edc.reporting.html import render
    page = render(output, catalog)
    if catalog.get('workflows'):
        from edc.reporting.html import esc
        links = []
        for item in reversed(catalog['workflows']):
            target = item.get('report', '')
            if target and not Path(target).is_absolute() and (output / target).resolve().is_relative_to(output):
                links.append('<li><a href="' + esc(target) + '">' + esc(item['workflow_id']) + ' · ' + esc(item['workflow_run_id']) + '</a></li>')
        section = '<section class="run"><h2>数据整理工作流</h2><ul>' + ''.join(links) + '</ul></section>'
        page = page.replace('</main>', section + '</main>')
    (output / "report.html").write_text(page, encoding="utf-8")
    return output / "report.html"
