import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { escapeHtml } from './utils.js';

marked.setOptions({
  gfm: true,
  breaks: false,
});

const renderer = new marked.Renderer();

// Sanitize javascript: URLs in links
const originalLink = renderer.link.bind(renderer);
renderer.link = function ({ href, title, tokens }) {
  if (href && /^\s*javascript\s*:/i.test(href)) {
    return tokens ? this.parser.parseInline(tokens) : '';
  }
  return originalLink({ href, title, tokens });
};

// Sanitize javascript: URLs in images, add lazy loading
renderer.image = function ({ href, title, text }) {
  if (href && /^\s*javascript\s*:/i.test(href)) {
    return text || '';
  }
  const titleAttr = title ? ' title="' + escapeHtml(title) + '"' : '';
  return '<img src="' + escapeHtml(href) + '" alt="' + escapeHtml(text || '') + '"' + titleAttr + ' loading="lazy">';
};

// Open links in new tab
const origLinkFn = renderer.link;
renderer.link = function (token) {
  const html = origLinkFn.call(this, token);
  if (html.startsWith('<a ') && !html.includes('target=')) {
    return html.replace('<a ', '<a target="_blank" rel="noopener" ');
  }
  return html;
};

marked.use({ renderer });

export function renderMarkdown(md) {
  if (!md) return '';
  const raw = marked.parse(md);
  return DOMPurify.sanitize(raw, { FORBID_TAGS: ['style'], FORBID_ATTR: ['style'] });
}
