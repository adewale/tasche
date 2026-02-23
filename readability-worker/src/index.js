import { WorkerEntrypoint } from 'cloudflare:workers';
import { Readability } from '@mozilla/readability';
import { parseHTML } from 'linkedom';

export class ReadabilityService extends WorkerEntrypoint {
  /**
   * Extract article content from raw HTML using Mozilla Readability.
   *
   * @param {string} html - Raw HTML of the fetched page
   * @param {string} url  - Final URL after redirects (for resolving relative URLs)
   * @returns {{ title: string, html: string, excerpt: string, byline: string|null }}
   */
  async parse(html, url) {
    const { document } = parseHTML(html);
    const reader = new Readability(document, { url });
    const article = reader.parse();
    if (!article) {
      return { title: '', html: '', excerpt: '', byline: null };
    }
    return {
      title: article.title || '',
      html: article.content || '',
      excerpt: article.excerpt || '',
      byline: article.byline || null,
    };
  }
}

export default {
  fetch() {
    return new Response('Readability Service — use via Service Binding RPC', {
      status: 200,
      headers: { 'Content-Type': 'text/plain' },
    });
  },
};
