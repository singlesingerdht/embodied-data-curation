"""Build EDC's existing Markdown guides without importing the runtime package."""
from pathlib import Path
import sys
import tomllib

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
sys.path.insert(0, str(DOCS / "_ext"))

project = "Embodied Data Curation"
author = "EDC contributors"
release = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
version = release
language = "zh_CN"
root_doc = "index"
extensions = ["myst_parser", "sphinx_design", "sphinxcontrib.mermaid", "site_support", "site_navigation", "site_redirects"]
source_suffix = {".md": "markdown"}
exclude_patterns = [
    "_build", "_templates", "presentations", "Thumbs.db", ".DS_Store",
    "STATUS.md", "**/STATUS.md", "**/*_INTEGRATION_PLAN_*.md",
    "**/*_MIGRATION_PLAN_*.md", "**/*_VALIDATION_*.md",
    "internal/**", "audits/**", "evidence/**",
]
myst_enable_extensions = ["colon_fence", "deflist"]
myst_fence_as_directive = ["mermaid"]
myst_heading_anchors = 5
nitpicky = True

html_theme = "pydata_sphinx_theme"
html_title = "EDC · Embodied Data Curation"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = ["navigation.js"]
html_favicon = "_static/favicon.svg"
templates_path = ["_templates"]
html_show_copyright = False
html_show_sourcelink = False
html_search_options = {"dict": str(DOCS / "search-dictionary.txt")}
html_theme_options = {
    "logo": {"text": "EDC"},
    "github_url": "https://github.com/singlesingerdht/embodied-data-curation",
    "navbar_start": ["site-logo.html"],
    "navbar_center": ["site-header.html"],
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    "navbar_persistent": ["site-language.html", "search-button"],
    "search_bar_text": "搜索 EDC 文档…",
    "navigation_depth": 2,
    "show_nav_level": 2,
    "show_toc_level": 2,
    "header_links_before_dropdown": 5,
    "show_prev_next": False,
    "footer_start": ["sphinx-version"],
    "footer_end": ["theme-version"],
}
html_sidebars = {"**": ["site-search.html", "site-navigation.html"]}
html_context = {"default_mode": "light"}
mermaid_version = "11.17.2"
