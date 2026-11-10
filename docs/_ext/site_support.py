"""Adapt repository Markdown links/anchors for Sphinx without duplicating guides.

GitHub and Sphinx treat raw HTML anchors differently. Give existing anchors
document-specific labels in the build and keep links to source/config files on
the repository's matching branch. No source Markdown is rewritten on disk.
"""
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from docutils.nodes import make_id

ANCHOR = re.compile(r'<a\s+id="([^"]+)"\s*>\s*</a>')
LINK = re.compile(r'(!?\[[^\]\n]*\]\()([^\s)]+)(\))')
REPOSITORY = "https://github.com/singlesingerdht/embodied-data-curation"
BRANCH = "leju-kuavo"


def label(path, anchor):
    return make_id(f"{path.stem}-{anchor}")


def prepare_source(app, docname, source):
    path = Path(app.env.doc2path(docname)).resolve()
    docs = Path(app.srcdir).resolve()
    root = docs.parent

    def rewrite(match):
        url = urlsplit(match[2])
        if url.scheme or url.netloc or match[2].startswith("{"):
            return match[0]
        target = (path.parent / unquote(url.path)).resolve() if url.path else path
        if target == root / "README.zh-CN.md":
            target = docs / "index.md"
        elif target == root / "README.md":
            target = docs / "index.en.md"
        if not target.is_relative_to(root):
            raise ValueError(f"Documentation link leaves repository: {docname}: {match[2]}")
        if target.is_relative_to(docs):
            # Existing guide Markdown is parsed by Sphinx; assets are copied by it.
            if target.suffix == ".md":
                fragment = url.fragment
                if fragment and target.exists():
                    anchors = ANCHOR.findall(target.read_text(encoding="utf-8"))
                    if fragment in anchors:
                        # MyST's file.md#fragment checks heading slugs only;
                        # #label resolves Sphinx's unique explicit targets.
                        return match[1] + "#" + label(target, fragment) + match[3]
                relative = target.relative_to(path.parent) if target.is_relative_to(path.parent) else None
                if relative is None:
                    import os
                    relative = Path(os.path.relpath(target, path.parent))
                address = urlunsplit(("", "", relative.as_posix(), url.query, fragment))
                return match[1] + address + match[3]
            if match[1].startswith("!"):
                return match[0]
        if not target.exists():
            raise FileNotFoundError(f"Missing repository link: {docname}: {match[2]}")
        kind = "tree" if target.is_dir() else "blob"
        address = f"{REPOSITORY}/{kind}/{BRANCH}/{quote(target.relative_to(root).as_posix())}"
        if url.fragment:
            address += "#" + url.fragment
        return match[1] + address + match[3]

    text = LINK.sub(rewrite, source[0])
    source[0] = ANCHOR.sub(lambda m: f"({label(path, m[1])})=", text)


def setup(app):
    app.connect("source-read", prepare_source)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
