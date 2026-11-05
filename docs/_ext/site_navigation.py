"""Language-aware navigation for the existing paired Markdown guides."""

PAIRS = {
    'index': 'index.en',
    'QUICKSTART.zh-CN': 'QUICKSTART',
    'DATA_AND_CONFIGURATION.zh-CN': 'DATA_AND_CONFIGURATION',
    'ALGORITHMS.zh-CN': 'ALGORITHMS',
    'DEVELOPMENT.zh-CN': 'DEVELOPMENT',
    'QUALITY_VALIDATION.zh-CN': 'QUALITY_VALIDATION',
}
SHARED = {'SELECTION', 'WORKFLOWS', 'EVIDENCE_AND_SCOPE', 'DOCUMENTATION', 'search', 'genindex'}
NAVIGATION = {
    'zh-CN': [
        ('使用指南', [
            ('QUICKSTART.zh-CN', '快速开始'),
            ('DATA_AND_CONFIGURATION.zh-CN', '数据与机器人配置'),
            ('ALGORITHMS.zh-CN', '算法推荐与执行'),
            ('SELECTION', '筛选与导出'),
        ]),
        ('开发与原理', [
            ('DEVELOPMENT.zh-CN', '算法开发'),
            ('WORKFLOWS', '工作流与复检'),
            ('EVIDENCE_AND_SCOPE', '证据与检查范围'),
            ('QUALITY_VALIDATION.zh-CN', '研究依据与验证'),
        ]),
    ],
    'en': [
        ('User guides', [
            ('QUICKSTART', 'Quick start'),
            ('DATA_AND_CONFIGURATION', 'Data and robot configuration'),
            ('ALGORITHMS', 'Recommendation and execution'),
            ('SELECTION', 'Selection and export'),
        ]),
        ('Development and principles', [
            ('DEVELOPMENT', 'Algorithm development'),
            ('WORKFLOWS', 'Workflows and rechecks (中文)'),
            ('EVIDENCE_AND_SCOPE', 'Evidence and scope (中文)'),
            ('QUALITY_VALIDATION', 'Research and validation'),
        ]),
    ],
}


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
            {'title': title, 'items': [
                {'href': address(doc, lang), 'title': title, 'active': doc == pagename}
                for doc, title in items
            ]} for title, items in groups
        ]
    context.update(
        language=locale,
        edc_locale=locale,
        edc_shared_page=pagename not in PAIRS and not english,
        edc_navigation=navigation,
        edc_language_links={lang: address(doc, lang) for lang, doc in counterparts.items()},
        edc_home_links={'zh-CN': pathto('index'), 'en': pathto('index.en')},
        edc_search_links={lang: address('search', lang) for lang in ('zh-CN', 'en')},
        edc_page_names=counterparts,
        root_doc='index.en' if english else 'index',
    )
    # The original toctree discovers every page; visible navigation is locale-specific.
    context['parents'] = []
    if english:
        context['_'] = lambda message: message
        context['theme_search_bar_text'] = 'Search EDC…'


def setup(app):
    # The theme renders navbar/sidebar components in its priority-500 handler.
    app.connect('html-page-context', page_context, priority=400)
    return {'version': '1', 'parallel_read_safe': True, 'parallel_write_safe': True}
