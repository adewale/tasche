/**
 * Unit tests for the markdown rendering module (frontend/src/markdown.js).
 *
 * Run with:
 *   cd frontend && npm test
 *
 * Uses jsdom to provide the DOM APIs that DOMPurify and escapeHtml require.
 * Dynamic import() is used so the DOM environment is set up before the
 * module under test is loaded.
 */

import { JSDOM } from 'jsdom';

// Set up a minimal DOM environment BEFORE importing modules that need it.
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser;
globalThis.Node = dom.window.Node;
globalThis.NodeFilter = dom.window.NodeFilter;
globalThis.HTMLElement = dom.window.HTMLElement;

// Dynamic import so that markdown.js sees the DOM globals above.
const { renderMarkdown } = await import('../../src/markdown.js');

let passed = 0;
let failed = 0;

function assert(condition, message) {
  if (condition) {
    passed++;
    console.log('  PASS: ' + message);
  } else {
    failed++;
    console.error('  FAIL: ' + message);
  }
}

function assertIncludes(haystack, needle, message) {
  assert(
    haystack.includes(needle),
    message + ' (expected to include "' + needle + '")'
  );
}

function assertNotIncludes(haystack, needle, message) {
  assert(
    !haystack.includes(needle),
    message + ' (expected NOT to include "' + needle + '")'
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

console.log('renderMarkdown: basic rendering');
{
  var result = renderMarkdown('# Hello World');
  assertIncludes(result, '<h1>', 'renders h1 heading tag');
  assertIncludes(result, 'Hello World', 'preserves heading text');
}

console.log('');
console.log('renderMarkdown: paragraphs and inline formatting');
{
  var result = renderMarkdown('This is **bold** and *italic* text.');
  assertIncludes(result, '<strong>bold</strong>', 'renders bold text');
  assertIncludes(result, '<em>italic</em>', 'renders italic text');
  assertIncludes(result, '<p>', 'wraps in paragraph');
}

console.log('');
console.log('renderMarkdown: links open in new tab');
{
  var result = renderMarkdown('[Example](https://example.com)');
  assertIncludes(result, 'target="_blank"', 'adds target="_blank" to links');
  assertIncludes(result, 'rel="noopener"', 'adds rel="noopener" to links');
  assertIncludes(result, 'href=', 'has href attribute');
  assertIncludes(result, 'example.com', 'preserves link URL');
  assertIncludes(result, '>Example</a>', 'preserves link text');
}

console.log('');
console.log('renderMarkdown: javascript: URLs are sanitized in links');
{
  var result = renderMarkdown('[Click me](javascript:alert(1))');
  assertNotIncludes(result, 'javascript:', 'strips javascript: URL from link');
  assertIncludes(result, 'Click me', 'preserves link text even when URL is sanitized');
}

console.log('');
console.log('renderMarkdown: javascript: URLs are sanitized in images');
{
  var result = renderMarkdown('![alt text](javascript:alert(1))');
  assertNotIncludes(result, 'javascript:', 'strips javascript: URL from image');
}

console.log('');
console.log('renderMarkdown: images get lazy loading');
{
  var result = renderMarkdown('![Photo](https://example.com/photo.jpg)');
  assertIncludes(result, 'loading="lazy"', 'adds loading="lazy" to images');
  assertIncludes(result, 'photo.jpg', 'preserves image src');
  assertIncludes(result, 'alt="Photo"', 'preserves alt text');
}

console.log('');
console.log('renderMarkdown: images with title');
{
  var result = renderMarkdown('![Photo](https://example.com/photo.jpg "A nice photo")');
  assertIncludes(result, 'title="A nice photo"', 'renders image title attribute');
}

console.log('');
console.log('renderMarkdown: code blocks');
{
  var result = renderMarkdown('```js\nconsole.log("hello");\n```');
  assertIncludes(result, '<pre>', 'renders pre tag for code block');
  assertIncludes(result, '<code', 'renders code tag');
  assertIncludes(result, 'language-js', 'adds language class to code block');
}

console.log('');
console.log('renderMarkdown: inline code');
{
  var result = renderMarkdown('Use `npm install` to install.');
  assertIncludes(result, '<code>npm install</code>', 'renders inline code');
}

console.log('');
console.log('renderMarkdown: unordered lists');
{
  var result = renderMarkdown('- First\n- Second\n- Third');
  assertIncludes(result, '<ul>', 'renders unordered list');
  assertIncludes(result, '<li>', 'renders list items');
}

console.log('');
console.log('renderMarkdown: ordered lists');
{
  var result = renderMarkdown('1. First\n2. Second\n3. Third');
  assertIncludes(result, '<ol>', 'renders ordered list');
}

console.log('');
console.log('renderMarkdown: blockquotes');
{
  var result = renderMarkdown('> This is a quote');
  assertIncludes(result, '<blockquote>', 'renders blockquote');
}

console.log('');
console.log('renderMarkdown: GFM tables');
{
  var result = renderMarkdown('| Name | Age |\n|------|-----|\n| Alice | 30 |');
  assertIncludes(result, '<table>', 'renders table');
  assertIncludes(result, '<th>', 'renders table header');
  assertIncludes(result, '<td>', 'renders table data');
}

console.log('');
console.log('renderMarkdown: horizontal rule');
{
  var result = renderMarkdown('---');
  assertIncludes(result, '<hr>', 'renders horizontal rule');
}

console.log('');
console.log('renderMarkdown: empty / falsy input');
{
  assert(renderMarkdown('') === '', 'returns empty string for empty input');
  assert(renderMarkdown(null) === '', 'returns empty string for null input');
  assert(renderMarkdown(undefined) === '', 'returns empty string for undefined input');
}

console.log('');
console.log('renderMarkdown: DOMPurify sanitization');
{
  var result = renderMarkdown('<script>alert("xss")</script>');
  assertNotIncludes(result, '<script>', 'strips script tags');
}

console.log('');
console.log('renderMarkdown: style tags and attributes are removed');
{
  var result = renderMarkdown('<div style="color:red">styled</div>');
  assertNotIncludes(result, 'style=', 'strips style attributes');
}

console.log('');
console.log('renderMarkdown: complex document with multiple elements');
{
  var md = [
    '# Article Title',
    '',
    'A paragraph with a [link](https://example.com) and **bold** text.',
    '',
    '## Section Two',
    '',
    '- Item one',
    '- Item two',
    '',
    '```python',
    'def hello():',
    '    print("world")',
    '```',
    '',
    '> A blockquote here.',
    '',
    '![Image](https://example.com/img.png "Title")',
  ].join('\n');

  var result = renderMarkdown(md);
  assertIncludes(result, '<h1>', 'complex doc: renders h1');
  assertIncludes(result, '<h2>', 'complex doc: renders h2');
  assertIncludes(result, 'target="_blank"', 'complex doc: links open in new tab');
  assertIncludes(result, '<ul>', 'complex doc: renders list');
  assertIncludes(result, 'language-python', 'complex doc: renders Python code block');
  assertIncludes(result, '<blockquote>', 'complex doc: renders blockquote');
  assertIncludes(result, 'loading="lazy"', 'complex doc: images have lazy loading');
  assert(result.length > 100, 'complex doc: produces substantial HTML output');
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

console.log('');
console.log('========================================');
console.log('Results: ' + passed + ' passed, ' + failed + ' failed');
console.log('========================================');

if (failed > 0) {
  process.exit(1);
}
