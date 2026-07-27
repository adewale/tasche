import { test } from 'node:test';
import assert from 'node:assert/strict';

import { extractArticle } from '../src/extract.js';

const ARTICLE_HTML = `<!doctype html>
<html>
  <head>
    <title>How Queues Fail | Example Blog</title>
    <meta name="author" content="Jane Author" />
  </head>
  <body>
    <nav><a href="/">Home</a><a href="/about">About</a></nav>
    <article>
      <h1>How Queues Fail</h1>
      <p class="byline">By Jane Author</p>
      <p>Message queues are deceptively simple until a consumer starts
      failing. This paragraph needs to be long enough that Readability
      treats the article element as the primary content of the page, so it
      keeps going with more sentences about retries, backoff, and dead
      letter queues than anyone strictly needs.</p>
      <p>A second substantial paragraph helps too. Readability scores
      content blocks by text length, link density, and paragraph count, so
      multiple meaty paragraphs make the extraction deterministic across
      library versions. <a href="relative/link">A relative link</a> for URL
      resolution.</p>
      <p>Third paragraph to push the content score safely over the
      threshold. Without enough text, Readability returns null and the
      service falls back to its empty contract object.</p>
    </article>
    <footer>Copyright Example Blog</footer>
  </body>
</html>`;

// The dict contract expected by SafeReadability.parse() in src/wrappers.py.
const CONTRACT_KEYS = ['title', 'html', 'excerpt', 'byline'];

test('extracts the article and returns the exact contract shape', () => {
  const result = extractArticle(ARTICLE_HTML, 'https://example.com/post/1');

  assert.deepEqual(Object.keys(result).sort(), [...CONTRACT_KEYS].sort());
  assert.equal(typeof result.title, 'string');
  assert.equal(typeof result.html, 'string');
  assert.equal(typeof result.excerpt, 'string');
  assert.ok(result.byline === null || typeof result.byline === 'string');

  assert.match(result.title, /How Queues Fail/);
  assert.match(result.html, /Message queues are deceptively simple/);
  assert.ok(result.excerpt.length > 0, 'excerpt should be non-empty');
});

test('strips page chrome (nav/footer) from the extracted content', () => {
  const result = extractArticle(ARTICLE_HTML, 'https://example.com/post/1');

  assert.doesNotMatch(result.html, /Copyright Example Blog/);
  assert.doesNotMatch(result.html, />About</);
});

test('returns the empty contract when there is no extractable article', () => {
  const result = extractArticle('<html></html>', 'https://example.com/');

  assert.deepEqual(result, { title: '', html: '', excerpt: '', byline: null });
});

test('returns the empty contract for empty input instead of throwing', () => {
  const result = extractArticle('', 'https://example.com/');

  assert.deepEqual(result, { title: '', html: '', excerpt: '', byline: null });
});

test('tolerates malformed HTML without throwing', () => {
  const result = extractArticle('<div><p>broken<span></div>', 'https://example.com/');

  assert.deepEqual(Object.keys(result).sort(), [...CONTRACT_KEYS].sort());
});
