// Preserve the selected language on shared guides and stable section anchors.
document.addEventListener('DOMContentLoaded', () => {
  const root = document.documentElement;
  const language = root.dataset.edcLanguage;
  if (language === 'en' && typeof Documentation !== 'undefined') {
    Documentation.TRANSLATIONS = {};
    Documentation.LOCALE = 'en';
  }
  const currentPage = decodeURIComponent(location.pathname.split('/').pop()).replace(/\.html$/, '');
  const shared = new Set(['SELECTION.html', 'WORKFLOWS.html', 'EVIDENCE_AND_SCOPE.html', 'DOCUMENTATION.html', 'search.html', 'genindex.html']);
  const paired = new Set(['QUICKSTART', 'DATA_AND_CONFIGURATION', 'ALGORITHMS', 'DEVELOPMENT', 'QUALITY_VALIDATION']);
  const slug = page => page.toLowerCase().replace(/[._]/g, '-');
  document.querySelectorAll('.edc-language a').forEach(link => {
    if (link.dataset.edcLanguage === language) link.setAttribute('aria-current', 'true');
    else link.removeAttribute('aria-current');
    link.addEventListener('click', () => {
      const target = new URL(link.href);
      const hash = decodeURIComponent(location.hash.slice(1));
      const prefix = slug(currentPage) + '-';
      const section = document.getElementById(hash)?.closest('section');
      // A translated heading and its explicit source anchor identify the same section.
      const markers = [hash, section?.id, ...Array.from(section?.querySelectorAll(':scope > span[id]') || [], node => node.id)];
      const marker = markers.find(id => id?.startsWith(prefix));
      if (marker) target.hash = slug(link.dataset.edcPage) + '-' + marker.slice(prefix.length);
      else if (target.pathname === location.pathname) target.hash = location.hash;
      const query = new URLSearchParams(location.search).get('q');
      if (query && currentPage === 'search') target.searchParams.set('q', query);
      link.href = target.href;
    });
  });
  // Keep the selected navigation on shared guides, including links from paired pages.
  function resolveLink(link) {
      const url = new URL(link.href, location.href);
      if (url.origin !== location.origin) return;
      const filename = decodeURIComponent(url.pathname.split('/').pop());
      const basename = filename.replace(/(?:\.zh-CN)?\.html$/, '');
      if (shared.has(filename)) {
        url.searchParams.set('lang', language);
      } else if (root.dataset.edcShared === 'true' && !['search', 'genindex'].includes(currentPage) && paired.has(basename)) {
        const target = basename + (language === 'en' ? '' : '.zh-CN');
        const source = filename.replace(/\.html$/, '');
        url.pathname = url.pathname.slice(0, -filename.length) + target + '.html';
        const prefix = slug(source) + '-';
        if (url.hash.slice(1).startsWith(prefix)) url.hash = slug(target) + '-' + url.hash.slice(1 + prefix.length);
      }
      link.href = url.href;
  }
  document.querySelectorAll('.bd-article a[href]').forEach(resolveLink);
  if (root.dataset.edcShared === 'true') {
    const home = document.querySelector('.breadcrumb-home a');
    if (home) home.href = language === 'en' ? 'index.en.html' : 'index.html';
    if (currentPage === 'search') document.title = (language === 'en' ? 'Search' : '搜索') + ' — EDC';
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
  if (searchResults) {
    // Search renders asynchronously; shared results should retain the language choice.
    new MutationObserver(() => {
      searchResults.querySelectorAll('a[href]').forEach(resolveLink);
    }).observe(searchResults, {childList: true, subtree: true});
  }
});
