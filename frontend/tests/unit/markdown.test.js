/**
 * Unit tests for the markdown rendering module (frontend/src/markdown.js).
 *
 * Migrated from hand-rolled assert() calls to vitest describe/it/expect.
 * The jsdom environment is provided by vitest (configured in vite.config.js).
 */

import { renderMarkdown } from '../../src/markdown.js';

describe('renderMarkdown', () => {
  describe('basic rendering', () => {
    it('renders h1 heading tag', () => {
      const result = renderMarkdown('# Hello World');
      expect(result).toContain('<h1>');
      expect(result).toContain('Hello World');
    });
  });

  describe('paragraphs and inline formatting', () => {
    it('renders bold and italic text', () => {
      const result = renderMarkdown('This is **bold** and *italic* text.');
      expect(result).toContain('<strong>bold</strong>');
      expect(result).toContain('<em>italic</em>');
      expect(result).toContain('<p>');
    });
  });

  describe('links open in new tab', () => {
    it('adds target and rel attributes to links', () => {
      const result = renderMarkdown('[Example](https://example.com)');
      expect(result).toContain('target="_blank"');
      expect(result).toContain('rel="noopener"');
      expect(result).toContain('href=');
      expect(result).toContain('example.com');
      expect(result).toContain('>Example</a>');
    });
  });

  describe('javascript: URLs are sanitized', () => {
    it('strips javascript: URL from link', () => {
      const result = renderMarkdown('[Click me](javascript:alert(1))');
      expect(result).not.toContain('javascript:');
      expect(result).toContain('Click me');
    });

    it('strips javascript: URL from image', () => {
      const result = renderMarkdown('![alt text](javascript:alert(1))');
      expect(result).not.toContain('javascript:');
    });
  });

  describe('images', () => {
    it('adds lazy loading to images', () => {
      const result = renderMarkdown('![Photo](https://example.com/photo.jpg)');
      expect(result).toContain('loading="lazy"');
      expect(result).toContain('photo.jpg');
      expect(result).toContain('alt="Photo"');
    });

    it('renders image title attribute', () => {
      const result = renderMarkdown('![Photo](https://example.com/photo.jpg "A nice photo")');
      expect(result).toContain('title="A nice photo"');
    });
  });

  describe('code blocks', () => {
    it('renders fenced code blocks with language class', () => {
      const result = renderMarkdown('```js\nconsole.log("hello");\n```');
      expect(result).toContain('<pre>');
      expect(result).toContain('<code');
      expect(result).toContain('language-js');
    });

    it('renders inline code', () => {
      const result = renderMarkdown('Use `npm install` to install.');
      expect(result).toContain('<code>npm install</code>');
    });
  });

  describe('lists', () => {
    it('renders unordered lists', () => {
      const result = renderMarkdown('- First\n- Second\n- Third');
      expect(result).toContain('<ul>');
      expect(result).toContain('<li>');
    });

    it('renders ordered lists', () => {
      const result = renderMarkdown('1. First\n2. Second\n3. Third');
      expect(result).toContain('<ol>');
    });
  });

  describe('blockquotes', () => {
    it('renders blockquote', () => {
      const result = renderMarkdown('> This is a quote');
      expect(result).toContain('<blockquote>');
    });
  });

  describe('GFM tables', () => {
    it('renders table with header and data', () => {
      const result = renderMarkdown('| Name | Age |\n|------|-----|\n| Alice | 30 |');
      expect(result).toContain('<table>');
      expect(result).toContain('<th>');
      expect(result).toContain('<td>');
    });
  });

  describe('horizontal rule', () => {
    it('renders horizontal rule', () => {
      const result = renderMarkdown('---');
      expect(result).toContain('<hr>');
    });
  });

  describe('empty / falsy input', () => {
    it('returns empty string for empty input', () => {
      expect(renderMarkdown('')).toBe('');
    });

    it('returns empty string for null input', () => {
      expect(renderMarkdown(null)).toBe('');
    });

    it('returns empty string for undefined input', () => {
      expect(renderMarkdown(undefined)).toBe('');
    });
  });

  describe('DOMPurify sanitization', () => {
    it('strips script tags', () => {
      const result = renderMarkdown('<script>alert("xss")</script>');
      expect(result).not.toContain('<script>');
    });

    it('strips style attributes', () => {
      const result = renderMarkdown('<div style="color:red">styled</div>');
      expect(result).not.toContain('style=');
    });
  });

  describe('complex document with multiple elements', () => {
    it('renders all element types correctly', () => {
      const md = [
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

      const result = renderMarkdown(md);
      expect(result).toContain('<h1>');
      expect(result).toContain('<h2>');
      expect(result).toContain('target="_blank"');
      expect(result).toContain('<ul>');
      expect(result).toContain('language-python');
      expect(result).toContain('<blockquote>');
      expect(result).toContain('loading="lazy"');
      expect(result.length).toBeGreaterThan(100);
    });
  });
});
