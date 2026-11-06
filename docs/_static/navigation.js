// Keep the chosen language on shared guides and across paired pages.
document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const language = root.dataset.edcLanguage;
  const shared = root.dataset.edcShared === 'true';
  const currentDoc = root.dataset.edcDoc || '';
  const pairs = JSON.parse(root.dataset.edcPairs || '[]');
  const sharedDocs = new Set(JSON.parse(root.dataset.edcSharedPages || '[]'));
  const pairFor = doc => pairs.find(pair => pair[0] === doc || pair[1] === doc);
  const slug = doc => doc.split('/').pop().toLowerCase().replace(/[._]/g, '-');

  if (language === 'en' && typeof Documentation !== 'undefined') {
    Documentation.TRANSLATIONS = {};
    Documentation.LOCALE = 'en';
  }

  // Resolve a rendered link to the docname Sphinx built it from.
  function docnameOf(link) {
    const href = link.getAttribute('href') || '';
    if (!href || href.startsWith('#') || /^[a-z][a-z0-9+.-]*:/i.test(href)) return '';
    const url = new URL(href, `https://doc.local/${currentDoc}.html`);
    return decodeURIComponent(url.pathname).replace(/^\//, '').replace(/\.html$/, '');
  }

  // Language switch: keep a matching section when the translated anchor exists.
  document.querySelectorAll('.edc-language a').forEach(link => {
    if (link.dataset.edcLanguage === language) link.setAttribute('aria-current', 'true');
    else link.removeAttribute('aria-current');
    link.addEventListener('click', () => {
      const targetDoc = link.dataset.edcPage;
      const target = new URL(link.href, location.href);
      const hash = decodeURIComponent(location.hash.slice(1));
      const section = document.getElementById(hash)?.closest('section');
      // A translated heading and its explicit source anchor identify the same section.
      const markers = [hash, section?.id, ...Array.from(section?.querySelectorAll(':scope > span[id]') || [], node => node.id)];
      const prefix = slug(currentDoc) + '-';
      const marker = markers.find(id => id?.startsWith(prefix));
      if (marker) target.hash = slug(targetDoc) + '-' + marker.slice(prefix.length);
      else if (target.pathname === location.pathname) target.hash = location.hash;
      const query = new URLSearchParams(location.search).get('q');
      if (query && currentDoc === 'search') {
        target.searchParams.set('q', query);
      }
      link.href = target.href;
    });
  });

  // Keep the chosen language on shared guides, and point shared-page body
  // links at the selected language's paired page.
  function resolveLink(link) {
    const targetDoc = docnameOf(link);
    if (!targetDoc) return;
    const url = new URL(link.href, location.href);
    if (sharedDocs.has(targetDoc)) {
      url.searchParams.set('lang', language);
      link.href = url.href;
      return;
    }
    const pair = pairFor(targetDoc);
    if (!pair || !shared) return;
    const isChinese = pair[0] === targetDoc;
    if ((isChinese && language === 'zh-CN') || (!isChinese && language === 'en')) return;
    const chosen = language === 'en' ? pair[1] : pair[0];
    const filename = decodeURIComponent(url.pathname.split('/').pop());
    const directory = url.pathname.slice(0, url.pathname.length - filename.length);
    url.pathname = directory + chosen.split('/').pop() + '.html';
    const hash = decodeURIComponent(url.hash.slice(1));
    const prefix = slug(targetDoc) + '-';
    if (hash.startsWith(prefix)) url.hash = slug(chosen) + '-' + hash.slice(prefix.length);
    link.href = url.href;
  }
  document.querySelectorAll('.bd-article a[href]').forEach(resolveLink);

  if (shared) {
    const home = document.querySelector('.breadcrumb-home a');
    if (home) home.href = language === 'en' ? 'index.en.html' : 'index.html';
    if (document.getElementById('search-results')) {
      document.title = (language === 'en' ? 'Search' : '搜索') + ' — EDC';
    }
  }

  // The theme also exposes a keyboard search overlay outside the sidebar.
  document.querySelectorAll('form.bd-search').forEach(form => {
    const choice = document.createElement('input');
    choice.type = 'hidden';
    choice.name = 'lang';
    choice.value = language;
    form.append(choice);
    const field = form.querySelector('input[name="q"]');
    field.placeholder = language === 'en' ? 'Search EDC…' : '搜索 EDC…';
    field.setAttribute('aria-label', field.placeholder);
  });
  document.querySelectorAll('.search-button').forEach(button => {
    const title = language === 'en' ? 'Search' : '搜索';
    button.title = title;
    button.setAttribute('aria-label', title);
  });

  const searchResults = document.getElementById('search-results');
  if (shared && searchResults) {
    // Search renders asynchronously; shared results should retain the language choice.
    new MutationObserver(() => {
      searchResults.querySelectorAll('a[href]').forEach(resolveLink);
    }).observe(searchResults, {childList: true, subtree: true});
  }
});
