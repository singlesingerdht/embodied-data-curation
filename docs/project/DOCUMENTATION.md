# 文档维护与发布

[返回主页](../../README.zh-CN.md) · [操作指南](../how-to/index.md) · [参考](../reference/index.md)

本站使用 Sphinx、MyST Markdown 和 PyData Sphinx Theme，直接构建 `docs/` 中现有中英文页面。
网站显示名称为 **EDC · Embodied Data Curation**，版本从 `pyproject.toml` 读取。

## 信息架构

文档按读者意图分区；首页四个入口与顶部、侧栏导航都由同一组页面构成：

| 区域 | 回答什么 | 目录 |
|---|---|---|
| 教程 | 第一次完整跑通 | `tutorial/` |
| 操作指南 | 完成一项具体任务 | `how-to/` |
| 参考 | 查格式、字段、算法、接口与命令 | `reference/` |
| 说明 | 理解背景、边界与设计取舍 | `explanation/` |
| 项目与维护 | 构建、发布与内容维护 | `project/` |

每个区域首页（`how-to/index.md`、`reference/index.md`、`explanation/index.md`）用任务或问题表格指路；教程只有一篇，因此直接由首页卡片和导航进入，不另设区域首页。

`STATUS.md` 保留本地验收记录，不参与站点构建；历史记录中的路径按当时状态保留，不随页面移动改写。原始数据、报告、模型和演示文稿不会自动打包进网站。

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
生成页面也可以从 `docs/_build/html/index.html` 打开，完整搜索请使用 HTTP 预览。

文档环境无需安装 EDC、GPU 包或模型。中文搜索使用 jieba 和 `search-dictionary.txt` 中的专业词。
Mermaid 脚本由 jsDelivr 加载固定版本，供 `how-to/WORKFLOWS.md` 中的工作流图渲染使用。
离线时该图无法绘制，正文、目录与搜索仍可使用本地构建资源。

## 新增或调整页面

1. **先定区域**：教程、操作指南、参考、说明各只承担一种意图。一页混合两种意图时优先拆分，用交叉链接互相指向。
2. **放置文件**：使用 `<区域>/<大写名>[.zh-CN].md`；中英文成对的页面放在同一目录、同名（中文加 `.zh-CN`）。
3. **写页面开头**：说明这篇要完成/回答什么、前提是什么，并链接到其他区域的对应页面。
4. **注册导航**：在 `_ext/site_navigation.py` 中登记——成对页面加入 `PAIRS`，单语正文加入 `SHARED`，再更新 `NAVIGATION`（分区菜单）和 `TOP_LINKS`（顶部入口）；构建会在菜单指向不存在页面时失败。
5. **加入 toctree**：页面必须且只能出现在一个 toctree 中。区域首页的隐藏 toctree 列出本区页面；根 `index.md` 只列出各区域首页、教程和项目页。
6. **检查链接**：站内链接使用相对 Markdown 链接；指向仓库文件、配置和源码的链接由 `_ext/site_support.py` 在构建时映射到对应分支。严格构建会报告失效链接。

## 语言切换

顶部“简体中文 / English”切换到同一篇页面的对应语言，已有对应锚点的章节保留位置；侧栏只显示所选语言的导航。

- `_ext/site_navigation.py` 维护页面配对（`PAIRS`）、单语页面（`SHARED`）和两份菜单。
- `layout.html` 把当前页与配对表写入页面数据；`_static/navigation.js` 据此改写正文链接、保留锚点与搜索词。
- 筛选导出、工作流、证据与命令行均提供独立中英文页面；共用的维护页通过 `?lang=en` 或 `?lang=zh-CN` 保持导航语言。
- 只有中文正文的页面在英文菜单中标注“中文”。新增单语页面时，务必同时加入 `SHARED`、`NAVIGATION` 和对应 toctree。

## 自动构建与发布

`.github/workflows/docs.yml` 对文档变更执行严格构建，生成可下载的 HTML artifact。
普通 push、pull request 和默认手动运行都只构建，发布默认关闭。

更新线上内容时：

1. 将文档修改合入默认分支 `main`。
2. 仓库 **Settings → Pages** 保持 **GitHub Actions**，使用现有 Sphinx 工作流发布。
3. 手动运行 **EDC documentation**，明确选择 `publish`，构建通过后再部署。

正式入口已发布：[使用指南](https://singlesingerdht.github.io/embodied-data-curation/)。
站点使用现有 Sphinx 工作流发布。

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
例如 `docs/tutorial/QUICKSTART.zh-CN.md` 对应 `tutorial/QUICKSTART.zh-CN.html`，
`docs/explanation/QUALITY_VALIDATION.md#automation` 对应
`explanation/QUALITY_VALIDATION.html#quality-validation-automation`。
图片、配置、源码、PPT 和 README 自身的语言切换链接保持仓库路径。
页面尚未发布或章节不存在时不修改 README；脚本不提交、不推送、不部署。

本地可先检查映射：

```bash
python docs/_tools/activate_readme_links.py --base-url http://127.0.0.1:8765/
```

本地预览时不要使用 `--apply`，以免 README 链接指向本机。
