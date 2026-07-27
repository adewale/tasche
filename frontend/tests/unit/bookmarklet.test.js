import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getBookmarkletCode } from '../../src/utils.js';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// ---------------------------------------------------------------------------
// getBookmarkletCode() — utility that generates the bookmarklet JS string
// ---------------------------------------------------------------------------

describe('getBookmarkletCode', () => {
  it('returns a javascript: URI', () => {
    const code = getBookmarkletCode();
    expect(code).toMatch(/^javascript:void\(open\(/);
  });

  it('includes the current origin', () => {
    const code = getBookmarkletCode();
    expect(code).toContain(window.location.origin);
  });

  it('encodes location.href and document.title', () => {
    const code = getBookmarkletCode();
    expect(code).toContain('encodeURIComponent(location.href)');
    expect(code).toContain('encodeURIComponent(document.title)');
  });

  it('opens popup at 420x480', () => {
    const code = getBookmarkletCode();
    expect(code).toContain('width=420');
    expect(code).toContain('height=480');
  });
});

// ---------------------------------------------------------------------------
// bookmarklet popup page — markup (bookmarklet.html) and logic
// (bookmarklet-page.js, external so the strict CSP applies to the page)
// ---------------------------------------------------------------------------

describe('bookmarklet.html', () => {
  let html;
  let pageScript;

  beforeEach(() => {
    const htmlPath = resolve(__dirname, '../../public/bookmarklet.html');
    html = readFileSync(htmlPath, 'utf-8');
    const scriptPath = resolve(__dirname, '../../public/bookmarklet-page.js');
    pageScript = readFileSync(scriptPath, 'utf-8');
  });

  it('loads its logic from an external script (CSP: no inline scripts)', () => {
    expect(html).toContain('src="/bookmarklet-page.js"');
    expect(html).not.toContain('<script>');
  });

  it('contains a form with title input', () => {
    expect(html).toContain('id="title-input"');
  });

  it('contains tag input with datalist', () => {
    expect(html).toContain('id="tag-input"');
    expect(html).toContain('id="tag-options"');
    expect(html).toContain('<datalist');
  });

  it('has Save and Save audio buttons', () => {
    expect(html).toContain('id="btn-save"');
    expect(html).toContain('id="btn-audio"');
  });

  it('fetches /api/tags for autocomplete', () => {
    expect(pageScript).toContain("fetch('/api/tags'");
  });

  it('fetches /api/tag-rules for suggestions', () => {
    expect(pageScript).toContain("fetch('/api/tag-rules'");
  });

  it('sends tag_ids in the POST body', () => {
    expect(pageScript).toContain('body.tag_ids = selectedTagIds');
  });

  it('sends listen_later when Save audio is clicked', () => {
    expect(pageScript).toContain('body.listen_later = true');
  });

  it('handles 401 by redirecting to main app', () => {
    expect(pageScript).toContain('r.status === 401');
    expect(pageScript).toContain("window.location.href = '/?url='");
  });

  it('handles 409 duplicate gracefully', () => {
    expect(pageScript).toContain('r.status === 409');
    expect(pageScript).toContain('Already saved.');
  });

  it('supports dark mode via CSS variables', () => {
    expect(html).toContain('prefers-color-scheme: dark');
  });

  it('implements domain rule matching', () => {
    expect(pageScript).toContain("rule.match_type === 'domain'");
    expect(pageScript).toContain("rule.match_type === 'title_contains'");
    expect(pageScript).toContain("rule.match_type === 'url_contains'");
  });

  it('closes on Escape key', () => {
    expect(pageScript).toContain("e.key === 'Escape'");
    expect(pageScript).toContain('window.close()');
  });

  it('supports Ctrl/Cmd+Enter for save audio', () => {
    expect(pageScript).toContain('e.ctrlKey || e.metaKey');
  });
});
