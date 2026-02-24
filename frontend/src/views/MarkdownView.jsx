import { useState, useEffect, useMemo } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { addToast } from '../state.js';
import { getArticle, getArticleMarkdown } from '../api.js';
import { renderMarkdown } from '../markdown.js';
import { IconArrowLeft, IconCopy, IconCheck } from '../components/Icons.jsx';

export function MarkdownView({ id }) {
  const [article, setArticle] = useState(null);
  const [markdown, setMarkdown] = useState('');
  const [loadError, setLoadError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [viewMode, setViewMode] = useState('rendered');

  const renderedHtml = useMemo(function () {
    if (!markdown) return null;
    try {
      return renderMarkdown(markdown);
    } catch (e) {
      return null;
    }
  }, [markdown]);

  useEffect(function () {
    let cancelled = false;

    async function load() {
      try {
        const art = await getArticle(id);
        if (cancelled) return;
        setArticle(art);

        const md = await getArticleMarkdown(id);
        if (cancelled) return;
        if (md) {
          setMarkdown(md);
        } else {
          setLoadError('No markdown content available for this article.');
        }
      } catch (e) {
        if (!cancelled) setLoadError(e.message);
      }
    }

    load();
    return function () { cancelled = true; };
  }, [id]);

  function handleCopy() {
    if (!markdown) return;
    navigator.clipboard.writeText(markdown).then(function () {
      setCopied(true);
      addToast('Markdown copied to clipboard', 'success');
      setTimeout(function () { setCopied(false); }, 2000);
    }).catch(function () {
      addToast('Failed to copy to clipboard', 'error');
    });
  }

  if (loadError) {
    return (
      <>
        <Header />
        <main class="main-content">
          <EmptyState title="Could not load markdown">
            {loadError}
            <br />
            <a href={'#/article/' + id} class="btn btn-secondary" style={{ marginTop: '16px' }}>
              Back to article
            </a>
          </EmptyState>
        </main>
      </>
    );
  }

  if (!article) {
    return (
      <>
        <Header />
        <main class="main-content">
          <LoadingSpinner />
        </main>
      </>
    );
  }

  return (
    <>
      <Header />
      <main class="main-content">
        <div class="markdown-view-header">
          <a href={'#/article/' + id} class="reader-back">
            <IconArrowLeft /> Back to article
          </a>
          <h1 class="markdown-view-title">{article.title || 'Untitled'}</h1>
          <div class="markdown-view-actions">
            <div class="markdown-view-tabs">
              <button
                class={'btn btn-sm ' + (viewMode === 'rendered' ? 'btn-primary' : 'btn-secondary')}
                onClick={function () { setViewMode('rendered'); }}
              >
                Rendered
              </button>
              <button
                class={'btn btn-sm ' + (viewMode === 'source' ? 'btn-primary' : 'btn-secondary')}
                onClick={function () { setViewMode('source'); }}
              >
                Source
              </button>
            </div>
            <button class="btn btn-sm btn-secondary" onClick={handleCopy}>
              {copied ? <><IconCheck size={14} /> Copied</> : <><IconCopy size={14} /> Copy Markdown</>}
            </button>
          </div>
        </div>
        {viewMode === 'rendered' && renderedHtml !== null ? (
          <div
            class="reader-content markdown-view-rendered"
            dangerouslySetInnerHTML={{ __html: renderedHtml }}
          />
        ) : (
          <pre class="markdown-view-content">{markdown}</pre>
        )}
      </main>
    </>
  );
}
