/**
 * Simple markdown-to-HTML renderer for basic display.
 * Ported from vanilla JS app.
 *
 * IMPORTANT: Image regex MUST run before link regex
 * because ![alt](url) contains [alt](url).
 */

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

export function renderMarkdown(md) {
  if (!md) return '';
  var html = escapeHtml(md);

  // Headers
  html = html.replace(/^######\s+(.+)$/gm, '<h6>$1</h6>');
  html = html.replace(/^#####\s+(.+)$/gm, '<h5>$1</h5>');
  html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');

  // Bold and italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Code blocks
  html = html.replace(/```[\w]*\n([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Blockquotes
  html = html.replace(/^&gt;\s+(.+)$/gm, '<blockquote>$1</blockquote>');

  // Horizontal rules
  html = html.replace(/^---$/gm, '<hr>');

  // Images MUST be processed before links because ![alt](url) contains [alt](url)
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (_, alt, url) {
    if (/^\s*javascript\s*:/i.test(url.replace(/&amp;/g, '&').replace(/&#/g, '#')))
      return alt;
    // Decode HTML entities but re-encode characters that could break out of attributes
    var decodedUrl = url
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"');
    var safeUrl = decodedUrl.replace(/"/g, '&quot;');
    return '<img src="' + safeUrl + '" alt="' + alt + '" loading="lazy">';
  });

  // Links (sanitize javascript: URLs, decode HTML entities in href)
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, text, url) {
    if (/^\s*javascript\s*:/i.test(url.replace(/&amp;/g, '&').replace(/&#/g, '#')))
      return text;
    // Decode HTML entities but re-encode characters that could break out of attributes
    var decodedUrl = url
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"');
    var safeUrl = decodedUrl.replace(/"/g, '&quot;');
    return '<a href="' + safeUrl + '" target="_blank" rel="noopener">' + text + '</a>';
  });

  // Unordered lists
  html = html.replace(/^[\-\*]\s+(.+)$/gm, '<li>$1</li>');
  html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');

  // Ordered lists
  html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');

  // Paragraphs: wrap remaining loose text lines
  html = html.replace(/^(?!<[a-z])((?:[^\n])+)$/gm, '<p>$1</p>');

  // Clean up empty paragraphs
  html = html.replace(/<p>\s*<\/p>/g, '');

  return html;
}
