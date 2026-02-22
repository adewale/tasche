import { useState, useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { TagPicker } from '../components/TagPicker.jsx';
import { playAudio } from '../components/AudioPlayer.jsx';
import { articles, currentArticle, addToast } from '../state.js';
import {
  IconArrowLeft, IconStar, IconExternalLink, IconPlay,
  IconHeadphones, IconClock, IconDownload, IconCheck, IconCamera,
  IconRefresh,
} from '../components/Icons.jsx';
import {
  getArticle,
  getArticleContent,
  updateArticle,
  deleteArticle as apiDeleteArticle,
  listenLater as apiListenLater,
  retryArticle as apiRetryArticle,
  checkOriginal as apiCheckOriginal,
  saveForOffline,
  saveAudioOffline,
  isOfflineCached,
} from '../api.js';
import DOMPurify from 'dompurify';
import { renderMarkdown } from '../markdown.js';
import { escapeHtml } from '../utils.js';

export function Reader({ id }) {
  const [article, setArticle] = useState(null);
  const [contentHtml, setContentHtml] = useState('');
  const [loadError, setLoadError] = useState(null);
  const [audioRequested, setAudioRequested] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [checkingOriginal, setCheckingOriginal] = useState(false);
  const [offlineStatus, setOfflineStatus] = useState({ cached: false, hasContent: false, hasAudio: false });
  const [savingOffline, setSavingOffline] = useState(false);
  const [savingAudioOffline, setSavingAudioOffline] = useState(false);

  useEffect(() => {
    const currentId = id;
    let scrollTimer = null;

    function handleScroll() {
      if (scrollTimer) clearTimeout(scrollTimer);
      scrollTimer = setTimeout(function () {
        if (currentId !== id) return;
        const scrollTop = window.scrollY || document.documentElement.scrollTop;
        const docHeight =
          document.documentElement.scrollHeight - document.documentElement.clientHeight;
        if (docHeight <= 0) return;
        const progress = Math.min(1, Math.max(0, scrollTop / docHeight));
        updateArticle(currentId, {
          scroll_position: Math.round(progress * 10000) / 10000,
          reading_progress: Math.round(progress * 100) / 100,
        }).catch(function () {});
      }, 1000);
    }

    loadArticle(currentId, handleScroll);
    isOfflineCached(currentId).then(function (status) {
      setOfflineStatus(status);
    });

    function handleSWMessage(event) {
      if (!event.data) return;
      if (event.data.type === 'OFFLINE_SAVED' && event.data.articleId === currentId) {
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
      if (event.data.type === 'OFFLINE_SAVE_ERROR' && event.data.articleId === currentId) {
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

    window.addEventListener('scroll', handleScroll);

    return function () {
      if (scrollTimer) {
        clearTimeout(scrollTimer);
      }
      window.removeEventListener('scroll', handleScroll);
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.removeEventListener('message', handleSWMessage);
      }
      currentArticle.value = null;
    };
  }, [id]);

  async function loadArticle(currentId, handleScroll) {
    try {
      const art = await getArticle(currentId);
      setArticle(art);
      currentArticle.value = art;

      let html = '';
      const r2Html = await getArticleContent(currentId);
      if (r2Html) {
        html = DOMPurify.sanitize(r2Html, { FORBID_TAGS: ['style'], FORBID_ATTR: ['style'] });
      } else if (art.markdown_content) {
        html = renderMarkdown(art.markdown_content);
      } else if (art.excerpt) {
        html = '<p>' + escapeHtml(art.excerpt) + '</p>';
      } else if (art.status === 'pending' || art.status === 'processing') {
        html = '<p style="color:var(--text-muted)">Article is being processed. Refresh in a moment.</p>';
      } else if (art.status === 'failed') {
        html = '<p style="color:var(--text-muted)">Processing failed. Use the Retry button above to try again.</p>';
      } else {
        html =
          '<p style="color:var(--text-muted)">No content available. <a href="' +
          escapeHtml(art.original_url) +
          '" target="_blank" rel="noopener noreferrer">View original</a></p>';
      }
      setContentHtml(html);

      if (art.reading_status === 'unread') {
        updateArticle(currentId, { reading_status: 'reading' }).catch(function () {});
      }

      if (art.scroll_position && parseFloat(art.scroll_position) > 0) {
        setTimeout(function () {
          const pct = parseFloat(art.scroll_position);
          const docHeight =
            document.documentElement.scrollHeight - document.documentElement.clientHeight;
          window.scrollTo(0, pct * docHeight);
        }, 100);
      }
    } catch (e) {
      setLoadError(e.message);
    }
  }

  async function handleFavorite() {
    if (!article) return;
    const newFav = !article.is_favorite;
    try {
      await updateArticle(id, { is_favorite: newFav });
      const updated = { ...article, is_favorite: newFav ? 1 : 0 };
      setArticle(updated);
      articles.value = articles.value.map((a) => a.id === id ? { ...a, is_favorite: updated.is_favorite } : a);
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  async function handleStatusChange(e) {
    try {
      const newStatus = e.target.value;
      await updateArticle(id, { reading_status: newStatus });
      setArticle({ ...article, reading_status: newStatus });
      articles.value = articles.value.map((a) => a.id === id ? { ...a, reading_status: newStatus } : a);
      addToast('Status updated', 'success');
    } catch (err) {
      addToast(err.message, 'error');
    }
  }

  async function handleListenLater() {
    try {
      await apiListenLater(id);
      addToast('Audio generation queued', 'success');
      setAudioRequested(true);
    } catch (e) {
      if (e.status === 409) {
        addToast('Audio generation is already in progress', 'info');
      } else {
        addToast(e.message, 'error');
      }
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

  async function handleRetry() {
    if (retrying) return;
    setRetrying(true);
    try {
      await apiRetryArticle(id);
      setArticle({ ...article, status: 'pending' });
      addToast('Article re-queued for processing', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setRetrying(false);
    }
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
            <a href="#/" class="btn btn-secondary" style={{ marginTop: '16px' }}>
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
            <IconArrowLeft /> Back to articles
          </a>
          <h1 class="reader-title">{article.title || 'Untitled'}</h1>
          <div class="reader-meta">
            {article.author && (
              <span class="reader-meta-item">{article.author}</span>
            )}
            {article.domain && (
              <span class="reader-meta-item">
                <a href={article.original_url} target="_blank" rel="noopener noreferrer">
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
                <a href={article.original_url} target="_blank" rel="noopener noreferrer">
                  View Original <IconExternalLink />
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
              <IconStar filled={!!isFav} size={14} /> {isFav ? 'Favorited' : 'Favorite'}
            </button>
            <select
              class="input input-inline-select"
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
                  ? <><IconCheck size={14} /> Saved offline</>
                  : <><IconDownload size={14} /> Save for offline</>}
            </button>
            {hasAudio && (
              <button class="btn btn-sm btn-secondary" onClick={handlePlayAudio}>
                <IconPlay size={14} /> Listen
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
                    ? <><IconCheck size={14} /> Audio offline</>
                    : <><IconDownload size={14} /> Download audio</>}
              </button>
            )}
            {canRequestAudio && (
              <button class="btn btn-sm btn-secondary" onClick={handleListenLater}>
                <IconHeadphones size={14} /> Listen Later
              </button>
            )}
            {audioPending && (
              <button class="btn btn-sm btn-secondary" disabled>
                <IconClock size={14} /> Generating audio...
              </button>
            )}
            <a
              href={article.original_url}
              target="_blank"
              rel="noopener noreferrer"
              class="btn btn-sm btn-secondary"
            >
              <IconExternalLink /> Original
            </a>
            {article.original_key && (
              <a
                href={'/api/articles/' + id + '/screenshot'}
                target="_blank"
                rel="noopener noreferrer"
                class="btn btn-sm btn-secondary"
              >
                <IconCamera size={14} /> Screenshot
              </a>
            )}
            {(article.status === 'failed' || article.status === 'pending') && (
              <button class="btn btn-sm btn-secondary" onClick={handleRetry} disabled={retrying}>
                <IconRefresh size={14} /> {retrying ? 'Retrying...' : 'Retry'}
              </button>
            )}
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
