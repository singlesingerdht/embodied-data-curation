"""Verify published guide links before switching both README files to Pages.

Run in the documentation environment. Without --apply this only previews the
change; source/configuration/image links and the README language switch stay local.
"""
import argparse
from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

DOCS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DOCS / "_ext"))
from site_navigation import SHARED
from site_support import ANCHOR, LINK, label

PAGES = "https://singlesingerdht.github.io/embodied-data-curation/"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        self.ids.update(value for key, value in attrs if key == "id")


def prepare(root, base_url):
    """Return proposed README text and the page/fragment targets to validate."""
    changes, targets = {}, {}
    for name, locale in [("README.md", "en"), ("README.zh-CN.md", "zh-CN")]:
        path = root / name
        original = path.read_text(encoding="utf-8")

        def rewrite(match):
            url = urlsplit(match[2])
            if (match[1].startswith("!") or url.scheme or url.netloc
                    or not url.path.startswith("docs/") or not url.path.endswith(".md")):
                return match[0]
            source = root / unquote(url.path)
            if not source.is_file():
                raise ValueError(f"Missing source: {source}")
            docname = source.relative_to(root / "docs").with_suffix("").as_posix()
            address = urljoin(base_url.rstrip("/") + "/", quote(docname) + ".html")
            fragment = url.fragment
            if fragment in ANCHOR.findall(source.read_text(encoding="utf-8")):
                fragment = label(source, fragment)
            targets.setdefault(address, set()).add(fragment)
            if docname in SHARED:
                address += "?lang=" + locale
            if fragment:
                address += "#" + fragment
            return match[1] + address + match[3]

        updated = LINK.sub(rewrite, original)
        if updated != original:
            changes[path] = updated
    return changes, targets


def verify(targets):
    for address, fragments in sorted(targets.items()):
        request = Request(address, headers={"User-Agent": "EDC-documentation-link-check"})
        with urlopen(request, timeout=15) as response:
            if response.headers.get_content_type() != "text/html":
                raise ValueError(f"Expected HTML: {address}")
            page = Page()
            page.feed(response.read().decode("utf-8"))
        if "main-content" not in page.ids:
            raise ValueError(f"Expected a built EDC guide: {address}")
        missing = fragments - page.ids - {""}
        if missing:
            raise ValueError(f"Missing sections at {address}: {sorted(missing)}")
        print(f"Verified {address}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=PAGES, help="Published Pages URL; local preview URLs can be checked too.")
    parser.add_argument("--apply", action="store_true", help="Update both READMEs only after all checks pass.")
    args = parser.parse_args()
    try:
        changes, targets = prepare(DOCS.parent, args.base_url)
        verify(targets)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Links not changed: {error}\n")
    if args.apply:
        for path, content in changes.items():
            path.write_text(content, encoding="utf-8")
    action = "Updated" if args.apply else "Ready to update (preview only)"
    print(f"{action}: {len(changes)} README files, {len(targets)} guide pages.")


if __name__ == "__main__":
    main()
