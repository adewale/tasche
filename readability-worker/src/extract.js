import { Readability } from '@mozilla/readability';
import { parseHTML } from 'linkedom';

/**
 * Extract article content from raw HTML using Mozilla Readability.
 *
 * This is the pure extraction logic behind the ReadabilityService RPC —
 * kept free of Workers runtime imports so it can be tested under Node.
 * The returned shape is the contract expected by SafeReadability.parse()
 * in src/wrappers.py: { title, html, excerpt, byline }.
 *
 * @param {string} html - Raw HTML of the fetched page
 * @param {string} url  - Final URL after redirects (for resolving relative URLs)
 * @returns {{ title: string, html: string, excerpt: string, byline: string|null }}
 */
const EMPTY_RESULT = Object.freeze({ title: '', html: '', excerpt: '', byline: null });

export function extractArticle(html, url) {
  try {
    const { document } = parseHTML(html);
    if (!document) {
      // linkedom returns no document for empty/degenerate input, and the
      // Readability constructor throws on a missing document.
      return { ...EMPTY_RESULT };
    }
    const reader = new Readability(document, { url });
    const article = reader.parse();
    if (!article) {
      return { ...EMPTY_RESULT };
    }
    return {
      title: article.title || '',
      html: article.content || '',
      excerpt: article.excerpt || '',
      byline: article.byline || null,
    };
  } catch {
    // Extraction failures must never break the RPC contract — the Python
    // caller treats an empty result as "fall back to BeautifulSoup".
    return { ...EMPTY_RESULT };
  }
}
