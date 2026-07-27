import { WorkerEntrypoint } from 'cloudflare:workers';
import { extractArticle } from './extract.js';

export class ReadabilityService extends WorkerEntrypoint {
  /**
   * Extract article content from raw HTML using Mozilla Readability.
   *
   * Delegates to extractArticle (src/extract.js), which is pure and
   * Node-testable; this class only provides the Workers RPC surface.
   *
   * @param {string} html - Raw HTML of the fetched page
   * @param {string} url  - Final URL after redirects (for resolving relative URLs)
   * @returns {{ title: string, html: string, excerpt: string, byline: string|null }}
   */
  async parse(html, url) {
    return extractArticle(html, url);
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
