import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { escapeHtml } from './utils.js';

marked.setOptions({
  gfm: true,
  breaks: false,
});

/**
 * Custom renderer overrides passed to marked.use().
 *
 * Uses a plain object (not a Renderer instance) so that marked's internal
 * renderer keeps its `this.parser` reference intact.  Returning `false`
 * from any method tells marked to fall back to the built-in renderer.
 */
marked.use({
  renderer: {
    // Sanitize javascript: URLs and open links in new tab
    link: function ({ href, title, tokens }) {
      if (href && /^\s*javascript\s*:/i.test(href)) {
        return tokens ? this.parser.parseInline(tokens) : '';
      }
      // Let the built-in renderer produce the HTML, then add target="_blank"
      const text = this.parser.parseInline(tokens);
      const cleanHref = href || '';
      let out = '<a target="_blank" rel="noopener" href="' + escapeHtml(cleanHref) + '"';
      if (title) {
        out += ' title="' + escapeHtml(title) + '"';
      }
      out += '>' + text + '</a>';
      return out;
    },

    // Sanitize javascript: URLs in images, add lazy loading
    image: function ({ href, title, text }) {
      if (href && /^\s*javascript\s*:/i.test(href)) {
        return text || '';
      }
      const titleAttr = title ? ' title="' + escapeHtml(title) + '"' : '';
      return (
        '<img src="' +
        escapeHtml(href) +
        '" alt="' +
        escapeHtml(text || '') +
        '"' +
        titleAttr +
        ' loading="lazy">'
      );
    },
  },
});

export function renderMarkdown(md) {
  if (!md) return '';
  const raw = marked.parse(md);
  return DOMPurify.sanitize(raw, {
    FORBID_TAGS: ['style'],
    FORBID_ATTR: ['style'],
    ADD_ATTR: ['target'],
  });
}
