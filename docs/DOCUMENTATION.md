# 页面维护与发布

本站使用 Sphinx、MyST Markdown 和 PyData Sphinx Theme，直接构建 `docs/` 中现有中英文指南。
网站显示名称为 **EDC · Embodied Data Curation**，版本从 `pyproject.toml` 读取。

## 本地构建

从仓库根目录运行，文档工具链使用 Python 3.13 或更高版本；框架本身仍支持 Python 3.10+：

```bash
python -m venv .venv-docs
source .venv-docs/bin/activate
python -m pip install -r requirements-docs.txt
python -m sphinx -n -W --keep-going -b html docs docs/_build/html
python -m http.server 8765 --bind 127.0.0.1 --directory docs/_build/html
```

浏览器访问 <http://127.0.0.1:8765/>。该地址属于运行命令的机器；远程主机需要端口转发。
服务仅监听本机，不会公开到互联网。生成页面也可以从 `docs/_build/html/index.html` 打开，
完整搜索请使用 HTTP 预览。

文档环境无需安装 EDC、GPU 包或模型。中文搜索使用 jieba 和 `search-dictionary.txt` 中的专业词。
Mermaid 脚本由 jsDelivr 加载固定版本，供 `WORKFLOWS.md` 中的工作流图渲染使用。
离线时该图无法绘制，正文、目录与搜索仍可使用本地构建资源。

## 更新内容

- 修改原有 `QUICKSTART.zh-CN.md`、`DATA_AND_CONFIGURATION.zh-CN.md`、`ALGORITHMS.zh-CN.md`
  等专题文件，网站下次构建直接更新，无需另外复制正文。
- `index.md` 与 `index.en.md` 分别维护中英文首页；图示继续使用 `docs/assets/`。
- 顶部“简体中文 / English”切换到同一篇指南，已有对应锚点的章节保留位置；侧栏只显示所选语言的导航。
  `_ext/site_navigation.py` 管理页面配对与两份菜单，`_templates/` 和 `_static/navigation.js` 负责呈现与跳转。
  `SELECTION.md` 共用双语正文；工作流、证据范围和维护说明目前只有中文，英文入口注明“中文”。
  共用页面通过 `?lang=en` 或 `?lang=zh-CN` 保持导航语言，搜索覆盖两种语言的正文。
- `_ext/site_support.py` 只在构建中适配 GitHub Markdown 锚点和源码链接，不改写原始 Markdown。
  配置、实现和插件源码链接指向本仓库的 `leju-kuavo` 分支，跟踪分支变化时同步调整该值。
- `STATUS.md` 保留本地验收记录，不参与站点构建。原始数据、报告、模型和演示文稿不会自动打包进网站。

## 自动构建与发布开关

`.github/workflows/docs.yml` 对文档变更执行严格构建，生成可下载的 HTML artifact。
普通 push、pull request 和默认手动运行都只构建，发布默认关闭。

以后确定要发布时：

1. 核对站点内容、访问范围及私有仓库的 GitHub Pages 计划支持。
2. 将文档与工作流合入默认分支 `main`；仓库 **Settings → Pages** 选择 **GitHub Actions**。
3. 手动运行 **EDC documentation**，明确选择 `publish`，构建通过后再部署。

正式入口使用 `https://singlesingerdht.github.io/embodied-data-curation/`，当前尚未发布。
私有仓库的 Pages 网站通常仍可公开访问，不能将私有仓库直接等同于私有网站。

参考：[GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。

## 发布后启用 README 页面跳转

README 直接使用“快速开始”“数据接入”“算法指南”“开发指南”等用途作为链接文字。
发布前保留仓库 Markdown 链接；部署成功后，在文档环境运行：

```bash
# 检查正式页面及章节是否可访问，只预览，不写文件。
python docs/_tools/activate_readme_links.py
# 所有目标可用后，替换两份 README 的指南链接。
python docs/_tools/activate_readme_links.py --apply
git diff -- README.md README.zh-CN.md
```

脚本保留链接文字，中文 README 进入中文页面、英文 README 进入英文页面；
例如 `docs/QUICKSTART.zh-CN.md` 对应 `QUICKSTART.zh-CN.html`，
`docs/QUALITY_VALIDATION.md#automation` 对应 `QUALITY_VALIDATION.html#quality-validation-automation`。
图片、配置、源码、PPT 和 README 自身的语言切换链接保持仓库路径。
页面尚未发布或章节不存在时不修改 README；脚本不提交、不推送、不部署。

本地可先检查映射：

```bash
python docs/_tools/activate_readme_links.py --base-url http://127.0.0.1:8765/
```

本地预览时不要使用 `--apply`，以免 README 链接指向本机。
