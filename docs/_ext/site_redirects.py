"""Keep published guide URLs working after the documentation reorganization."""
from html import escape
import json
from pathlib import Path

from site_support import ANCHOR, label


def routes():
    pages = {}
    for suffix in ("", ".zh-CN"):
        pages[f"QUICKSTART{suffix}"] = (f"tutorial/QUICKSTART{suffix}", {})
        pages[f"QUALITY_VALIDATION{suffix}"] = (
            f"explanation/QUALITY_VALIDATION{suffix}", {})
        pages[f"ALGORITHMS{suffix}"] = (f"reference/ALGORITHMS{suffix}", {
            "selection": f"how-to/RUN_AND_SELECT{suffix}",
            "results": f"how-to/RUN_AND_SELECT{suffix}",
        })
        pages[f"DATA_AND_CONFIGURATION{suffix}"] = (
            f"how-to/DATA_AND_ROBOT{suffix}", {
                "formats": f"reference/DATA_AND_ROBOTS{suffix}",
                "units-time": f"reference/DATA_AND_ROBOTS{suffix}",
            })
        pages[f"DEVELOPMENT{suffix}"] = (f"how-to/ALGORITHM_PLUGIN{suffix}", {
            "interfaces": f"reference/OUTPUTS{suffix}",
            "outputs": f"reference/OUTPUTS{suffix}",
            "readers": f"how-to/READER_PLUGIN{suffix}",
            "quality-example": f"how-to/ALGORITHM_PLUGIN{suffix}",
        })
    pages.update({
        "SELECTION": ("how-to/SELECTION", {}),
        "EVIDENCE_AND_SCOPE": ("reference/EVIDENCE_AND_SCOPE", {}),
        "WORKFLOWS": ("explanation/ARCHITECTURE.zh-CN", {}),
        "DOCUMENTATION": ("project/DOCUMENTATION", {}),
    })
    return pages


def write_redirects(app, exception):
    if exception is not None or app.builder.name != "html":
        return
    for old, (target, overrides) in routes().items():
        # Fail publication rather than emit a redirect to a missing page.
        sections = {}
        for docname in {target, *overrides.values()}:
            if not (Path(app.outdir) / f"{docname}.html").is_file():
                raise ValueError(f"Missing redirect target: {docname}")
            path = Path(app.srcdir) / f"{docname}.md"
            for anchor in ANCHOR.findall(path.read_text(encoding="utf-8")):
                if docname == overrides.get(anchor, target):
                    address = f"{docname}.html#{label(path, anchor)}"
                    sections[anchor] = address
                    sections[label(Path(old + ".md"), anchor)] = address
        for anchor, docname in overrides.items():
            sections.setdefault(anchor, f"{docname}.html")
            sections.setdefault(label(Path(old + ".md"), anchor), sections[anchor])
        address = target + ".html"
        html = f'''<!doctype html>
<html lang="en"><meta charset="utf-8">
<title>EDC — page moved</title>
<link rel="canonical" href="{escape(address, quote=True)}">
<script>
const sections = {json.dumps(sections)};
const anchor = decodeURIComponent(location.hash.slice(1));
const target = new URL(sections[anchor] || {json.dumps(address)}, location.href);
if (["SELECTION", "EVIDENCE_AND_SCOPE"].includes({json.dumps(old)}) &&
    new URLSearchParams(location.search).get("lang") === "en") {{
  target.pathname = target.pathname.replace(/\\.html$/, ".en.html");
}}
target.search = location.search;
if (!sections[anchor]) target.hash = location.hash;
location.replace(target.href);
</script>
<noscript><meta http-equiv="refresh" content="0; url={escape(address, quote=True)}"></noscript>
<a href="{escape(address, quote=True)}">Continue to the guide / 查看使用指南</a>
</html>
'''
        (Path(app.outdir) / f"{old}.html").write_text(html, encoding="utf-8")


def setup(app):
    app.connect("build-finished", write_redirects)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
