import { useState, useEffect, useRef } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { TagPicker } from '../components/TagPicker.jsx';
import { playAudio } from '../components/AudioPlayer.jsx';
import { currentArticle, addToast } from '../state.js';
import {
  getArticle,
  getArticleContent,
  updateArticle,
  deleteArticle as apiDeleteArticle,
  listenLater as apiListenLater,
  checkOriginal as apiCheckOriginal,
  saveForOffline,
  saveAudioOffline,
  isOfflineCached,
} from '../api.js';
import { renderMarkdown } from '../markdown.js';
import { escapeHtml } from '../utils.js';

export function Reader({ id }) {
  const [article, setArticle] = useState(null);
  const [contentHtml, setContentHtml] = useState('');
  const [loadError, setLoadError] = useState(null);
  const [audioRequested, setAudioRequested] = useState(false);
  const [checkingOriginal, setCheckingOriginal] = useState(false);
  const [offlineStatus, setOfflineStatus] = useState({ cached: false, hasContent: false, hasAudio: false });
  const [savingOffline, setSavingOffline] = useState(false);
  const [savingAudioOffline, setSavingAudioOffline] = useState(false);
  const scrollTimerRef = useRef(null);

  useEffect(() => {
    loadArticle();
    // Check offline cache status
    isOfflineCached(id).then(function (status) {
      setOfflineStatus(status);
    });

    // Listen for SW messages about offline save results
    function handleSWMessage(event) {
      if (!event.data) return;
      if (event.data.type === 'OFFLINE_SAVED' && event.data.articleId === id) {
        if (event.data.what === 'content') {
          setOfflineStatus(function (prev) { return { ...prev, cached: true, hasContent: true }; });
          setSavingOffline(false);
          addToast('Article saved for offline reading', 'success');
        } else if (event.data.what === 'audio') {
          setOfflineStatus(function (prev) { return { ...prev, cached: true, hasAudio: true }; });
          setSavingAudioOffline(false);
          addToast('Audio downloaded for offline listening', 'success');
        }
      }
      if (event.data.type === 'OFFLINE_SAVE_ERROR' && event.data.articleId === id) {
        if (event.data.what === 'content') {
          setSavingOffline(false);
          addToast('Failed to save for offline', 'error');
        } else if (event.data.what === 'audio') {
          setSavingAudioOffline(false);
          addToast('Failed to download audio', 'error');
        }
      }
    }

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.addEventListener('message', handleSWMessage);
    }

    return () => {
      // Clean up scroll tracking
      if (scrollTimerRef.current) {
        clearTimeout(scrollTimerRef.current);
      }
      window.removeEventListener('scroll', handleScroll);
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.removeEventListener('message', handleSWMessage);
      }
    };
  }, [id]);

  async function loadArticle() {
    try {
      const art = await getArticle(id);
      setArticle(art);
      currentArticle.value = art;

      // Determine content to show: try R2 HTML first, fall back to markdown
      let html = '';
      const r2Html = await getArticleContent(id);
      if (r2Html) {
        html = r2Html;
      } else if (art.markdown_content) {
        html = renderMarkdown(art.markdown_content);
      } else if (art.excerpt) {
        html = '<p>' + escapeHtml(art.excerpt) + '</p>';
      } else if (art.status === 'pending') {
        html = '<p class="text-muted">Article is being processed. Refresh in a moment.</p>';
      } else {
        html =
          '<p class="text-muted">No content available. <a href="' +
          escapeHtml(art.original_url) +
          '" target="_blank" rel="noopener">View original</a></p>';
      }
      setContentHtml(html);

      // Mark as reading if currently unread
      if (art.reading_status === 'unread') {
        updateArticle(id, { reading_status: 'reading' }).catch(() => {});
      }

      // Restore scroll position
      if (art.scroll_position && parseFloat(art.scroll_position) > 0) {
        setTimeout(() => {
          const pct = parseFloat(art.scroll_position);
          const docHeight =
            document.documentElement.scrollHeight - document.documentElement.clientHeight;
          const targetScroll = pct * docHeight;
          window.scrollTo(0, targetScroll);
        }, 100);
      }

      // Set up scroll tracking
      window.addEventListener('scroll', handleScroll);
    } catch (e) {
      setLoadError(e.message);
    }
  }

  function handleScroll() {
    if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current);
    scrollTimerRef.current = setTimeout(() => {
      const scrollTop = window.scrollY || document.documentElement.scrollTop;
      const docHeight =
        document.documentElement.scrollHeight - document.documentElement.clientHeight;
      if (docHeight <= 0) return;
      const progress = Math.min(1, Math.max(0, scrollTop / docHeight));
      updateArticle(id, {
        scroll_position: Math.round(progress * 10000) / 10000,
        reading_progress: Math.round(progress * 100) / 100,
      }).catch(() => {});
    }, 1000);
  }

  async function handleFavorite() {
    if (!article) return;
    const newFav = !article.is_favorite;
    try {
      await updateArticle(id, { is_favorite: newFav });
      setArticle({ ...article, is_favorite: newFav ? 1 : 0 });
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  async function handleStatusChange(e) {
    try {
      await updateArticle(id, { reading_status: e.target.value });
      setArticle({ ...article, reading_status: e.target.value });
      addToast('Status updated', 'success');
    } catch (e2) {
      addToast(e2.message, 'error');
    }
  }

  async function handleListenLater() {
    try {
      await apiListenLater(id);
      addToast('Audio generation queued', 'success');
      setAudioRequested(true);
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  function handlePlayAudio() {
    playAudio(id, article ? article.title : '');
  }

  async function handleDelete() {
    if (!confirm('Delete this article?')) return;
    try {
      await apiDeleteArticle(id);
      addToast('Article deleted', 'success');
      window.location.hash = '#/';
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  function handleSaveOffline() {
    if (offlineStatus.hasContent) {
      addToast('Already saved for offline', 'info');
      return;
    }
    setSavingOffline(true);
    saveForOffline(id);
  }

  function handleSaveAudioOffline() {
    if (offlineStatus.hasAudio) {
      addToast('Audio already downloaded for offline', 'info');
      return;
    }
    setSavingAudioOffline(true);
    saveAudioOffline(id);
  }

  async function handleCheckOriginal() {
    if (checkingOriginal) return;
    setCheckingOriginal(true);
    try {
      const result = await apiCheckOriginal(id);
      setArticle({ ...article, original_status: result.original_status });
      addToast('Original status checked', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setCheckingOriginal(false);
    }
  }

  if (loadError) {
    return (
      <>
        <Header />
        <main class="main-content">
          <div class="empty-state">
            <div class="empty-state-title">Could not load article</div>
            <div class="empty-state-text">{loadError}</div>
            <a href="#/" class="btn btn-secondary mt-4">
              Back to articles
            </a>
          </div>
        </main>
      </>
    );
  }

  if (!article) {
    return (
      <>
        <Header />
        <main class="main-content">
          <div class="loading">
            <div class="spinner"></div>
          </div>
        </main>
      </>
    );
  }

  const readingTime = article.reading_time_minutes
    ? article.reading_time_minutes + ' min read'
    : '';
  const statusClass = article.reading_status || 'unread';
  const isFav = article.is_favorite;
  const ostatus = article.original_status || 'unknown';
  const hasAudio = article.audio_status === 'ready';
  const canRequestAudio =
    !audioRequested &&
    article.audio_status !== 'pending' &&
    article.audio_status !== 'generating' &&
    article.audio_status !== 'ready';
  const audioPending =
    audioRequested ||
    article.audio_status === 'pending' ||
    article.audio_status === 'generating';

  return (
    <>
      <Header />
      <main class="main-content">
        <div class="reader-header">
          <a href="#/" class="reader-back">
            {'\u2190'} Back to articles
          </a>
          <h1 class="reader-title">{article.title || 'Untitled'}</h1>
          <div class="reader-meta">
            {article.author && (
              <span class="reader-meta-item">{article.author}</span>
            )}
            {article.domain && (
              <span class="reader-meta-item">
                <a href={article.original_url} target="_blank" rel="noopener">
                  {article.domain}
                </a>
              </span>
            )}
            {readingTime && <span class="reader-meta-item">{readingTime}</span>}
            {article.word_count && (
              <span class="reader-meta-item">
                {article.word_count.toLocaleString()} words
              </span>
            )}
          </div>
          <div class={'original-status original-status--' + ostatus}>
            {ostatus === 'available' && (
              <span>
                Original available{' '}
                <a href={article.original_url} target="_blank" rel="noopener">
                  View Original {'\u2197'}
                </a>
              </span>
            )}
            {ostatus === 'paywalled' && (
              <span>Original requires subscription</span>
            )}
            {ostatus === 'gone' && (
              <span>Original no longer available. Good thing you saved it.</span>
            )}
            {ostatus === 'domain_dead' && (
              <span>Source website is offline</span>
            )}
            {ostatus === 'unknown' && (
              <span>
                Original status unknown{' '}
                <button
                  class="btn btn-sm btn-secondary"
                  onClick={handleCheckOriginal}
                  disabled={checkingOriginal}
                >
                  {checkingOriginal ? 'Checking...' : 'Check now'}
                </button>
              </span>
            )}
          </div>
          <div class="reader-actions">
            <button
              class={'btn btn-sm ' + (isFav ? 'btn-primary' : 'btn-secondary')}
              onClick={handleFavorite}
            >
              {isFav ? '\u2605 Favorited' : '\u2606 Favorite'}
            </button>
            <select
              class="input"
              style="width:auto;padding:4px 10px;font-size:0.8125rem;"
              value={statusClass}
              onChange={handleStatusChange}
            >
              <option value="unread">Unread</option>
              <option value="reading">Reading</option>
              <option value="archived">Archived</option>
            </select>
            <button
              class={'btn btn-sm offline-btn' + (offlineStatus.hasContent ? ' offline-btn--saved' : '')}
              onClick={handleSaveOffline}
              disabled={savingOffline}
            >
              {savingOffline
                ? 'Saving...'
                : offlineStatus.hasContent
                  ? '\u2713 Saved offline'
                  : '\u2913 Save for offline'}
            </button>
            {hasAudio && (
              <button class="btn btn-sm btn-secondary" onClick={handlePlayAudio}>
                {'\u25B6'} Listen
              </button>
            )}
            {hasAudio && (
              <button
                class={'btn btn-sm offline-btn' + (offlineStatus.hasAudio ? ' offline-btn--saved' : '')}
                onClick={handleSaveAudioOffline}
                disabled={savingAudioOffline}
              >
                {savingAudioOffline
                  ? 'Downloading...'
                  : offlineStatus.hasAudio
                    ? '\u2713 Audio offline'
                    : '\u2913 Download audio'}
              </button>
            )}
            {canRequestAudio && (
              <button class="btn btn-sm btn-secondary" onClick={handleListenLater}>
                {'\uD83C\uDFA7'} Listen Later
              </button>
            )}
            {audioPending && (
              <span class="btn btn-sm btn-secondary" disabled>
                {'\u23F3'} Generating audio...
              </span>
            )}
            <a
              href={article.original_url}
              target="_blank"
              rel="noopener"
              class="btn btn-sm btn-secondary"
            >
              {'\u2197'} Original
            </a>
            <button class="btn btn-sm btn-danger" onClick={handleDelete}>
              Delete
            </button>
          </div>
          <TagPicker articleId={id} />
        </div>
        <article
          class="reader-content"
          dangerouslySetInnerHTML={{ __html: contentHtml }}
        />
      </main>
    </>
  );
}
