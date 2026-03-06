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
// bookmarklet.html inline script — core logic tests
// ---------------------------------------------------------------------------

describe('bookmarklet.html', () => {
  let html;

  beforeEach(() => {
    const htmlPath = resolve(__dirname, '../../public/bookmarklet.html');
    html = readFileSync(htmlPath, 'utf-8');
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
    expect(html).toContain("fetch('/api/tags'");
  });

  it('fetches /api/tag-rules for suggestions', () => {
    expect(html).toContain("fetch('/api/tag-rules'");
  });

  it('sends tag_ids in the POST body', () => {
    expect(html).toContain('body.tag_ids = selectedTagIds');
  });

  it('sends listen_later when Save audio is clicked', () => {
    expect(html).toContain('body.listen_later = true');
  });

  it('handles 401 by redirecting to main app', () => {
    expect(html).toContain('r.status === 401');
    expect(html).toContain("window.location.href = '/?url='");
  });

  it('handles 409 duplicate gracefully', () => {
    expect(html).toContain('r.status === 409');
    expect(html).toContain('Already saved.');
  });

  it('supports dark mode via CSS variables', () => {
    expect(html).toContain('prefers-color-scheme: dark');
  });

  it('implements domain rule matching', () => {
    expect(html).toContain("rule.match_type === 'domain'");
    expect(html).toContain("rule.match_type === 'title_contains'");
    expect(html).toContain("rule.match_type === 'url_contains'");
  });

  it('closes on Escape key', () => {
    expect(html).toContain("e.key === 'Escape'");
    expect(html).toContain('window.close()');
  });

  it('supports Ctrl/Cmd+Enter for save audio', () => {
    expect(html).toContain('e.ctrlKey || e.metaKey');
  });
});
