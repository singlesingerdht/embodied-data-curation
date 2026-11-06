"""Intent-based navigation for the paired Markdown guides.

The site organizes guides by what the reader wants to do (tutorial, how-to,
reference, explanation). Pages that exist in both languages are listed in
PAIRS; pages with one shared body are listed in SHARED. Adding a page means
adding it to one of those sets, to the matching NAVIGATION menu, and to a
toctree; the build fails if a menu target does not exist.
"""
from pathlib import Path

PAIRS = {
    'how-to/SELECTION': 'how-to/SELECTION.en',
    'how-to/WORKFLOWS': 'how-to/WORKFLOWS.en',
    'reference/CLI': 'reference/CLI.en',
    'reference/EVIDENCE_AND_SCOPE': 'reference/EVIDENCE_AND_SCOPE.en',
    'index': 'index.en',
    'how-to/index': 'how-to/index.en',
    'reference/index': 'reference/index.en',
    'explanation/index': 'explanation/index.en',
    'tutorial/QUICKSTART.zh-CN': 'tutorial/QUICKSTART',
    'how-to/DATA_AND_ROBOT.zh-CN': 'how-to/DATA_AND_ROBOT',
    'how-to/RUN_AND_SELECT.zh-CN': 'how-to/RUN_AND_SELECT',
    'how-to/ALGORITHM_PLUGIN.zh-CN': 'how-to/ALGORITHM_PLUGIN',
    'how-to/READER_PLUGIN.zh-CN': 'how-to/READER_PLUGIN',
    'reference/DATA_AND_ROBOTS.zh-CN': 'reference/DATA_AND_ROBOTS',
    'reference/ALGORITHMS.zh-CN': 'reference/ALGORITHMS',
    'reference/OUTPUTS.zh-CN': 'reference/OUTPUTS',
    'explanation/ARCHITECTURE.zh-CN': 'explanation/ARCHITECTURE',
    'explanation/QUALITY_VALIDATION.zh-CN': 'explanation/QUALITY_VALIDATION',
}
SHARED = {
    'project/DOCUMENTATION',
    'search',
    'genindex',
}
TOP_LINKS = {
    'zh-CN': [
        ('tutorial/QUICKSTART.zh-CN', '教程'),
        ('how-to/index', '操作指南'),
        ('reference/index', '参考'),
        ('explanation/index', '说明'),
    ],
    'en': [
        ('tutorial/QUICKSTART', 'Tutorial'),
        ('how-to/index.en', 'How-to'),
        ('reference/index.en', 'Reference'),
        ('explanation/index.en', 'Explanation'),
    ],
}
NAVIGATION = {
    'zh-CN': [
        {'title': '教程', 'items': [
            ('tutorial/QUICKSTART.zh-CN', '快速开始'),
        ]},
        {'title': '操作指南', 'index': 'how-to/index', 'items': [
            ('how-to/DATA_AND_ROBOT.zh-CN', '接入数据并配置机器人'),
            ('how-to/RUN_AND_SELECT.zh-CN', '推荐、选择并运行算法'),
            ('how-to/SELECTION', '按报告筛选并导出数据'),
            ('how-to/WORKFLOWS', '编排并运行质量工作流'),
            ('how-to/ALGORITHM_PLUGIN.zh-CN', '接入新质量算法'),
            ('how-to/READER_PLUGIN.zh-CN', '接入新的数据格式读取器'),
        ]},
        {'title': '参考', 'index': 'reference/index', 'items': [
            ('reference/DATA_AND_ROBOTS.zh-CN', '数据格式与机器人配置'),
            ('reference/ALGORITHMS.zh-CN', '内置质量算法'),
            ('reference/OUTPUTS.zh-CN', '输出对象与接口'),
            ('reference/CLI', '命令行'),
            ('reference/EVIDENCE_AND_SCOPE', '检查范围与证据参数'),
        ]},
        {'title': '说明', 'index': 'explanation/index', 'items': [
            ('explanation/QUALITY_VALIDATION.zh-CN', '研究依据与验证'),
            ('explanation/ARCHITECTURE.zh-CN', '框架结构与设计取舍'),
        ]},
        {'title': '项目与维护', 'items': [
            ('project/DOCUMENTATION', '文档维护与发布'),
        ]},
    ],
    'en': [
        {'title': 'Tutorial', 'items': [
            ('tutorial/QUICKSTART', 'Quick start'),
        ]},
        {'title': 'How-to guides', 'index': 'how-to/index.en', 'items': [
            ('how-to/DATA_AND_ROBOT', 'Connect data and configure a robot'),
            ('how-to/RUN_AND_SELECT', 'Recommend, select, and run algorithms'),
            ('how-to/SELECTION.en', 'Select and export data'),
            ('how-to/WORKFLOWS.en', 'Workflows and rechecks'),
            ('how-to/ALGORITHM_PLUGIN', 'Add a quality algorithm'),
            ('how-to/READER_PLUGIN', 'Add a reader plugin'),
        ]},
        {'title': 'Reference', 'index': 'reference/index.en', 'items': [
            ('reference/DATA_AND_ROBOTS', 'Data formats and robot configuration'),
            ('reference/ALGORITHMS', 'Built-in quality algorithms'),
            ('reference/OUTPUTS', 'Output objects and interfaces'),
            ('reference/CLI.en', 'Command line'),
            ('reference/EVIDENCE_AND_SCOPE.en', 'Scope and evidence'),
        ]},
        {'title': 'Explanation', 'index': 'explanation/index.en', 'items': [
            ('explanation/QUALITY_VALIDATION', 'Research and validation'),
            ('explanation/ARCHITECTURE', 'Framework structure and design'),
        ]},
        {'title': 'Project', 'items': [
            ('project/DOCUMENTATION', 'Documentation maintenance'),
        ]},
    ],
}


def _check_targets(app):
    """Fail the build when a menu points at a page that does not exist."""
    docs = set(PAIRS) | set(PAIRS.values()) | (SHARED - {'search', 'genindex'})
    for _locale, groups in NAVIGATION.items():
        for group in groups:
            docs.update(group.get('index', '').split())
            docs.update(doc for doc, _title in group['items'])
    for doc in TOP_LINKS['zh-CN'] + TOP_LINKS['en']:
        docs.add(doc[0])
    docs.discard('')
    missing = [doc for doc in sorted(docs) if not any(
        (Path(app.srcdir) / f'{doc}{suffix}').is_file() for suffix in ('.md', '.rst'))]
    if missing:
        raise RuntimeError(f'site_navigation: missing pages: {missing}')


def page_context(app, pagename, templatename, context, doctree):
    english = pagename in PAIRS.values()
    locale = 'en' if english else 'zh-CN'
    pathto = context['pathto']
    if pagename in PAIRS:
        counterparts = {'zh-CN': pagename, 'en': PAIRS[pagename]}
    elif english:
        counterparts = {'zh-CN': next(k for k, v in PAIRS.items() if v == pagename), 'en': pagename}
    else:
        counterparts = {'zh-CN': pagename, 'en': pagename}

    def address(doc, lang):
        href = pathto(doc)
        return href.split('#', 1)[0] + '?lang=' + lang if doc in SHARED else href

    navigation = {}
    for lang, groups in NAVIGATION.items():
        navigation[lang] = [
            {
                'title': group['title'],
                'href': address(group['index'], lang) if 'index' in group else '',
                'items': [
                    {'href': address(doc, lang), 'title': title, 'active': doc == pagename}
                    for doc, title in group['items']
                ],
            } for group in groups
        ]
    top_navigation = {
        lang: [
            {'href': address(doc, lang), 'title': title, 'active': doc == pagename}
            for doc, title in TOP_LINKS[lang]
        ] for lang in TOP_LINKS
    }
    context.update(
        language=locale,
        edc_locale=locale,
        edc_shared_page=pagename not in PAIRS and not english,
        edc_navigation=navigation,
        edc_top_navigation=top_navigation,
        edc_language_links={lang: address(doc, lang) for lang, doc in counterparts.items()},
        edc_home_links={'zh-CN': pathto('index'), 'en': pathto('index.en')},
        edc_search_links={lang: address('search', lang) for lang in ('zh-CN', 'en')},
        edc_page_names=counterparts,
        edc_current_docname=pagename,
        edc_shared_pages=sorted(SHARED),
        edc_page_pairs=[[zh, en] for zh, en in PAIRS.items()],
        root_doc='index.en' if english else 'index',
    )
    # The original toctree discovers every page; visible navigation is locale-specific.
    context['parents'] = []
    if english:
        context['_'] = lambda message: message
        context['theme_search_bar_text'] = 'Search EDC…'


def setup(app):
    # The theme renders navbar/sidebar components in its priority-500 handler.
    app.connect('builder-inited', _check_targets)
    app.connect('html-page-context', page_context, priority=400)
    return {'version': '2', 'parallel_read_safe': True, 'parallel_write_safe': True}
