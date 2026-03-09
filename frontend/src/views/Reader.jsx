import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { TagPicker } from '../components/TagPicker.jsx';
import { ReaderToolbar } from '../components/ReaderToolbar.jsx';
import { audioState, playAudio, getAudio } from '../components/AudioPlayer.jsx';
import { addToast, pollAudioStatus, searchQuery } from '../state.js';
import { toggleArchive, toggleFavorite, removeArticle } from '../articleActions.js';
import { readerPrefs, getReaderStyle, updatePref } from '../readerPrefs.js';
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts.js';
import { useSWMessage } from '../hooks/useSWMessage.js';
import { nav } from '../nav.js';
import {
  IconArrowLeft,
  IconStar,
  IconExternalLink,
  IconPlay,
  IconHeadphones,
  IconClock,
  IconOffline,
  IconRefresh,
  IconInkDrop,
  IconTrash,
  IconArchive,
} from '../components/Icons.jsx';
import {
  getArticle,
  getArticleContent,
  updateArticle,
  listenLater as apiListenLater,
  retryArticle as apiRetryArticle,
  checkOriginal as apiCheckOriginal,
  saveForOffline,
  removeFromOffline,
  saveAudioOffline,
  isOfflineCached,
  getArticleMarkdown,
  getAudioTiming,
} from '../api.js';
import { initImmersive, destroyImmersive } from '../immersive.js';
import DOMPurify from 'dompurify';
import { renderMarkdown } from '../markdown.js';
import { escapeHtml, formatDate } from '../utils.js';

/**
 * Breath marks: small tick marks in the margin at positions where
 * the reader previously paused. Stored per-article in localStorage.
 * Each return visit adds a mark; older marks fade (decreasing opacity).
 */
var BREATH_MARKS_KEY = 'tasche-breath-marks';
var MAX_BREATH_MARKS = 5;

function loadBreathMarks(articleId) {
  try {
    var all = JSON.parse(localStorage.getItem(BREATH_MARKS_KEY) || '{}');
    return (all[articleId] || []).slice(-MAX_BREATH_MARKS);
  } catch (_e) {
    return [];
  }
}

function saveBreathMark(articleId, position) {
  if (!position || position <= 0) return;
  try {
    var all = JSON.parse(localStorage.getItem(BREATH_MARKS_KEY) || '{}');
    var marks = all[articleId] || [];
    // Don't add if very close to the last mark
    var last = marks[marks.length - 1];
    if (last && Math.abs(last - position) < 0.02) return;
    marks.push(position);
    all[articleId] = marks.slice(-MAX_BREATH_MARKS);
    localStorage.setItem(BREATH_MARKS_KEY, JSON.stringify(all));
  } catch (_e) {
    // localStorage full or unavailable
  }
}

/**
 * Clear all search-highlight marks from a container, restoring original text nodes.
 */
function clearSearchHighlights(container) {
  if (!container) return;
  var marks = container.querySelectorAll('mark.search-highlight');
  for (var i = 0; i < marks.length; i++) {
    var mark = marks[i];
    var parent = mark.parentNode;
    if (parent) {
      parent.replaceChild(document.createTextNode(mark.textContent), mark);
      parent.normalize();
    }
  }
}

/**
 * Highlight search terms in a DOM container by wrapping matches in <mark> elements.
 * Uses a TreeWalker to find text nodes and splits them around matches.
 */
function highlightSearchTerms(container, query) {
  if (!container || !query) return;

  var terms = query
    .trim()
    .split(/\s+/)
    .filter(function (t) {
      return t.length > 1;
    });
  if (terms.length === 0) return;

  // Build a regex that matches any of the terms, case-insensitive
  var escaped = terms.map(function (t) {
    return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  });
  var regex = new RegExp('(' + escaped.join('|') + ')', 'gi');

  // Collect text nodes first (mutating the DOM while walking is unsafe)
  var walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
  var textNodes = [];
  var node;
  while ((node = walker.nextNode())) {
    // Skip nodes inside <script>, <style>, or already-highlighted marks
    var parentTag = node.parentElement ? node.parentElement.tagName : '';
    if (parentTag === 'SCRIPT' || parentTag === 'STYLE' || parentTag === 'MARK') continue;
    if (regex.test(node.nodeValue)) {
      textNodes.push(node);
    }
    regex.lastIndex = 0;
  }

  for (var i = 0; i < textNodes.length; i++) {
    var textNode = textNodes[i];
    var text = textNode.nodeValue;
    var frag = document.createDocumentFragment();
    var lastIndex = 0;
    var match;

    regex.lastIndex = 0;
    while ((match = regex.exec(text)) !== null) {
      // Text before the match
      if (match.index > lastIndex) {
        frag.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
      }
      // The matched term wrapped in <mark>
      var mark = document.createElement('mark');
      mark.className = 'search-highlight';
      mark.textContent = match[0];
      frag.appendChild(mark);
      lastIndex = regex.lastIndex;
    }
    // Remaining text after last match
    if (lastIndex < text.length) {
      frag.appendChild(document.createTextNode(text.slice(lastIndex)));
    }

    textNode.parentNode.replaceChild(frag, textNode);
  }
}

export function Reader({ id }) {
  const [article, setArticle] = useState(null);
  const [contentHtml, setContentHtml] = useState('');
  const [loadError, setLoadError] = useState(null);
  const [audioRequested, setAudioRequested] = useState(false);
  const [listeningLoading, setListeningLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [checkingOriginal, setCheckingOriginal] = useState(false);
  const [offlineStatus, setOfflineStatus] = useState({
    cached: false,
    hasContent: false,
    hasAudio: false,
  });
  const [savingOffline, setSavingOffline] = useState(false);
  const [savingAudioOffline, setSavingAudioOffline] = useState(false);
  const [markdownHtml, setMarkdownHtml] = useState(null);
  const [markdownRaw, setMarkdownRaw] = useState(null);
  const [markdownLoading, setMarkdownLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [breathMarks, setBreathMarks] = useState([]);
  const [showContinueReading, setShowContinueReading] = useState(false);
  const [immersiveTiming, setImmersiveTiming] = useState(null);
  const contentRef = useRef(null);

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

    loadArticle(currentId);
    isOfflineCached(currentId).then(function (status) {
      setOfflineStatus(status);
    });
    setBreathMarks(loadBreathMarks(currentId));

    window.addEventListener('scroll', handleScroll);

    return function () {
      if (scrollTimer) {
        clearTimeout(scrollTimer);
      }
      window.removeEventListener('scroll', handleScroll);
      // Save a breath mark at the current scroll position on unmount
      var scrollTop = window.scrollY || document.documentElement.scrollTop;
      var docHeight = document.documentElement.scrollHeight - document.documentElement.clientHeight;
      if (docHeight > 0) {
        var pos = Math.min(1, Math.max(0, scrollTop / docHeight));
        saveBreathMark(currentId, Math.round(pos * 10000) / 10000);
      }
    };
  }, [id]);

  // Service worker messages for offline save status
  useSWMessage(
    useCallback(
      function (event) {
        if (!event.data) return;
        if (event.data.type === 'OFFLINE_SAVED' && event.data.articleId === id) {
          if (event.data.what === 'content') {
            setOfflineStatus(function (prev) {
              return { ...prev, cached: true, hasContent: true };
            });
            setSavingOffline(false);
            addToast('Article saved for offline reading', 'success');
          } else if (event.data.what === 'audio') {
            setOfflineStatus(function (prev) {
              return { ...prev, cached: true, hasAudio: true };
            });
            setSavingAudioOffline(false);
            addToast('Audio downloaded for offline listening', 'success');
          }
        }
        if (event.data.type === 'OFFLINE_SAVE_ERROR' && event.data.articleId === id) {
          if (event.data.what === 'content') {
            setSavingOffline(false);
            addToast('Could not save article offline. Check your storage settings.', 'error');
          } else if (event.data.what === 'audio') {
            setSavingAudioOffline(false);
            addToast('Could not download audio for offline. The file may be too large.', 'error');
          }
        }
        if (event.data.type === 'OFFLINE_REMOVED' && event.data.articleId === id) {
          setOfflineStatus(function (prev) {
            return { ...prev, cached: false, hasContent: false };
          });
          setSavingOffline(false);
          addToast('Removed from offline', 'success');
        }
        if (event.data.type === 'OFFLINE_REMOVE_ERROR' && event.data.articleId === id) {
          setSavingOffline(false);
          addToast('Could not remove offline cache', 'error');
        }
      },
      [id],
    ),
  );

  // Keyboard shortcuts for Reader
  useKeyboardShortcuts(
    {
      Escape: function () {
        nav.library();
      },
      h: function () {
        nav.library();
      },
      a: function () {
        handleArchiveToggle();
      },
      s: function () {
        handleFavorite();
      },
      m: function () {
        var current = readerPrefs.value.contentMode || 'html';
        var next = current === 'html' ? 'markdown' : current === 'markdown' ? 'source' : 'html';
        updatePref('contentMode', next);
      },
    },
    [article],
  );

  // Immersive reading: activate when audio plays for this article
  useEffect(
    function () {
      var contentNode = contentRef.current;
      var audioInfo = audioState.value;
      var prefs = readerPrefs.value;
      var isThisArticle = audioInfo.articleId === id;
      var immersiveEnabled = prefs.immersive !== 'off' && prefs.contentMode !== 'source';

      if (!isThisArticle || !audioInfo.visible || !immersiveEnabled) {
        destroyImmersive();
        if (contentNode) {
          contentNode.closest('.reader-body')?.classList.remove('tts-playing');
        }
        return;
      }

      // Fetch timing data and init
      var cancelled = false;

      function activate() {
        if (immersiveTiming && contentNode) {
          initImmersive(contentNode, immersiveTiming, getAudio());
          contentNode.closest('.reader-body')?.classList.add('tts-playing');
        } else if (!immersiveTiming) {
          getAudioTiming(id)
            .then(function (timing) {
              if (!cancelled) {
                setImmersiveTiming(timing);
              }
            })
            .catch(function () {
              // No timing data — audio plays without highlighting
            });
        }
      }

      activate();

      return function () {
        cancelled = true;
        destroyImmersive();
        if (contentNode) {
          contentNode.closest('.reader-body')?.classList.remove('tts-playing');
        }
      };
    },
    // audioState and readerPrefs are @preact/signals — reading .value during
    // render subscribes the component automatically, so they don't belong in
    // the dependency array.
    [id, immersiveTiming],
  );

  // Lazy-load markdown content when user switches to Rendered or Source mode
  useEffect(
    function () {
      var mode = readerPrefs.value.contentMode;
      if (mode !== 'markdown' && mode !== 'source') return;
      if (markdownRaw !== null || markdownLoading) return;
      if (!article) return;

      setMarkdownLoading(true);
      getArticleMarkdown(id)
        .then(function (md) {
          var raw = md || article.markdown_content || '';
          setMarkdownRaw(raw);
          if (raw) {
            setMarkdownHtml(renderMarkdown(raw));
          } else {
            setMarkdownHtml(
              '<p class="reader-status-message">No markdown version available for this article.</p>',
            );
          }
        })
        .catch(function () {
          setMarkdownRaw('');
          setMarkdownHtml('<p class="reader-status-message">Could not load markdown version.</p>');
        })
        .finally(function () {
          setMarkdownLoading(false);
        });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [readerPrefs.value.contentMode, article, id, markdownRaw, markdownLoading],
  );

  // Search term highlighting — when arriving from search results
  useEffect(
    function () {
      var query = searchQuery.value;
      var container = contentRef.current;
      if (!article || !container || !query) return;

      highlightSearchTerms(container, query);

      // Scroll to the first highlight
      var firstMark = container.querySelector('mark.search-highlight');
      if (firstMark) {
        firstMark.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }

      return function () {
        clearSearchHighlights(container);
        searchQuery.value = '';
      };
    },
    [article, contentHtml],
  );

  async function handleArchiveToggle() {
    if (!article) return;
    var newStatus = article.reading_status === 'archived' ? 'unread' : 'archived';
    await toggleArchive(article);
    setArticle({ ...article, reading_status: newStatus });
  }

  async function loadArticle(currentId) {
    try {
      const art = await getArticle(currentId);
      setArticle(art);

      let html = '';
      const r2Html = await getArticleContent(currentId);
      if (r2Html) {
        html = DOMPurify.sanitize(r2Html, { FORBID_TAGS: ['style'], FORBID_ATTR: ['style'] });
      } else if (art.markdown_content) {
        html = renderMarkdown(art.markdown_content);
      } else if (art.excerpt) {
        html = '<p>' + escapeHtml(art.excerpt) + '</p>';
      } else if (art.status === 'pending' || art.status === 'processing') {
        html =
          '<p class="reader-status-message">Article is being processed. Refresh in a moment.</p>';
      } else if (art.status === 'failed') {
        html =
          '<p class="reader-status-message">Processing failed. Use the Retry button above to try again.</p>';
      } else {
        html =
          '<p class="reader-status-message">No content available. <a href="' +
          escapeHtml(art.original_url) +
          '" target="_blank" rel="noopener noreferrer">View original</a></p>';
      }
      setContentHtml(html);

      const scrollPct = art.scroll_position ? parseFloat(art.scroll_position) : 0;
      const readingProg = art.reading_progress ? parseFloat(art.reading_progress) : 0;
      if (scrollPct > 0.05 && readingProg < 0.95) {
        setShowContinueReading(true);
      } else if (scrollPct > 0) {
        setTimeout(function () {
          const docHeight =
            document.documentElement.scrollHeight - document.documentElement.clientHeight;
          window.scrollTo(0, scrollPct * docHeight);
        }, 100);
      }
    } catch (e) {
      setLoadError(e.message);
    }
  }

  async function handleFavorite() {
    if (!article) return;
    const newFav = !article.is_favorite ? 1 : 0;
    await toggleFavorite(article);
    setArticle({ ...article, is_favorite: newFav });
  }

  async function handleListenLater() {
    if (listeningLoading) return;
    setListeningLoading(true);
    try {
      await apiListenLater(id);
      addToast('Audio generation queued', 'success');
      setAudioRequested(true);
      pollAudioStatus(id, async function (articleId) {
        var updated = await getArticle(articleId);
        setArticle(updated);
        return updated;
      });
    } catch (e) {
      if (e.status === 409) {
        addToast('Audio generation is already in progress', 'info');
      } else {
        addToast(e.message, 'error');
      }
    } finally {
      setListeningLoading(false);
    }
  }

  function handlePlayAudio() {
    playAudio(
      id,
      article ? article.title : '',
      article ? article.domain : '',
      article ? article.thumbnail_key : null,
    );
  }

  async function handleDelete() {
    if (deleting) return;
    setDeleting(true);
    try {
      const deleted = await removeArticle(id);
      if (deleted) nav.library();
    } finally {
      setDeleting(false);
    }
  }

  function handleSaveOffline() {
    if (offlineStatus.hasContent) {
      setSavingOffline(true);
      removeFromOffline(id);
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
      const updates = { ...article, status: 'pending' };
      if (article.audio_status) {
        updates.audio_status = 'pending';
        updates.audio_key = null;
        updates.audio_duration_seconds = null;
      }
      setArticle(updates);
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

  function handleScrollToPosition() {
    if (!article) return;
    const pct = parseFloat(article.scroll_position);
    const docHeight = document.documentElement.scrollHeight - document.documentElement.clientHeight;
    window.scrollTo(0, pct * docHeight);
    setShowContinueReading(false);
  }

  if (loadError) {
    return (
      <>
        <Header readerMode />
        <main class="main-content">
          <EmptyState title="Could not load article">
            {loadError}
            <br />
            <a href="#/" class="btn btn-secondary mt-4">
              Back to articles
            </a>
          </EmptyState>
        </main>
      </>
    );
  }

  if (!article) {
    return (
      <>
        <Header readerMode />
        <main class="main-content">
          <LoadingSpinner />
        </main>
      </>
    );
  }

  const readingTime = article.reading_time_minutes
    ? article.reading_time_minutes + ' min read'
    : '';
  const isArchived = article.reading_status === 'archived';
  const isFav = article.is_favorite;
  const ostatus = article.original_status || 'unknown';
  const hasAudio = article.audio_status === 'ready';
  const audioPending = audioRequested || article.audio_status === 'pending';
  const audioStuck = !audioRequested && article.audio_status === 'generating';
  const audioFailed = article.audio_status === 'failed';

  return (
    <>
      <Header readerMode />
      <main class="main-content">
        <div class="reader-header">
          <a href="#/" class="reader-back">
            <IconArrowLeft /> Back to articles
          </a>
          <h1 class="reader-title">{article.title || 'Untitled'}</h1>
          <div class="reader-meta">
            {article.author && <span class="reader-meta-item">{article.author}</span>}
            {article.domain && (
              <span class="reader-meta-item">
                <a href={article.original_url} target="_blank" rel="noopener noreferrer">
                  {article.domain}
                </a>
              </span>
            )}
            {readingTime && <span class="reader-meta-item">{readingTime}</span>}
            {article.word_count && (
              <span class="reader-meta-item">{article.word_count.toLocaleString()} words</span>
            )}
            {article._cachedAt && (
              <span
                class="reader-meta-item cached-indicator"
                title={'Cached copy from ' + new Date(article._cachedAt).toLocaleString()}
              >
                <IconInkDrop size={10} /> saved copy · {formatDate(article._cachedAt)}
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
            {ostatus === 'paywalled' && <span>Original requires subscription</span>}
            {ostatus === 'gone' && (
              <span>Original no longer available. Good thing you saved it.</span>
            )}
            {ostatus === 'domain_dead' && <span>Source website is offline</span>}
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
          <TagPicker articleId={id} />
          <div class="reader-actions">
            <div class="reader-actions-group">
              <button
                class={'btn btn-sm ' + (isFav ? 'btn-primary' : 'btn-secondary')}
                onClick={handleFavorite}
              >
                <IconStar filled={!!isFav} size={14} /> {isFav ? 'Favourited' : 'Favourite'}
              </button>
              <button
                class={'btn btn-sm ' + (isArchived ? 'btn-primary' : 'btn-secondary')}
                onClick={handleArchiveToggle}
              >
                <IconArchive filled={isArchived} size={14} /> {isArchived ? 'Archived' : 'Archive'}
              </button>
              <button class={'btn btn-sm'} onClick={handleSaveOffline} disabled={savingOffline}>
                {savingOffline ? (
                  'Saving...'
                ) : offlineStatus.hasContent ? (
                  <>
                    <IconOffline size={14} filled /> Remove offline
                  </>
                ) : (
                  <>
                    <IconOffline size={14} /> Save for offline
                  </>
                )}
              </button>
            </div>
            <div class="reader-actions-group">
              {hasAudio && (
                <button class="btn btn-sm btn-secondary" onClick={handlePlayAudio}>
                  <IconPlay size={14} /> Listen
                </button>
              )}
              {hasAudio && (
                <button
                  class="btn btn-sm"
                  onClick={handleSaveAudioOffline}
                  disabled={savingAudioOffline}
                >
                  {savingAudioOffline ? (
                    'Downloading...'
                  ) : offlineStatus.hasAudio ? (
                    <>
                      <IconOffline size={14} filled /> Audio offline
                    </>
                  ) : (
                    <>
                      <IconOffline size={14} /> Download audio
                    </>
                  )}
                </button>
              )}
              {audioFailed && <span class="reader-status-inline">Processing failed</span>}
              {!audioPending && (
                <button
                  class="btn btn-sm btn-secondary"
                  onClick={handleListenLater}
                  disabled={listeningLoading}
                >
                  {listeningLoading ? (
                    <>
                      <IconClock size={14} /> Requesting...
                    </>
                  ) : hasAudio || audioFailed || audioStuck ? (
                    <>
                      <IconRefresh size={14} /> Regenerate audio
                    </>
                  ) : (
                    <>
                      <IconHeadphones size={14} /> Listen Later
                    </>
                  )}
                </button>
              )}
              {audioPending && (
                <button class="btn btn-sm btn-secondary" disabled>
                  <IconClock size={14} /> Generating audio...
                </button>
              )}
            </div>
            <div class="reader-actions-group">
              <a
                href={article.original_url}
                target="_blank"
                rel="noopener noreferrer"
                class="btn btn-sm btn-secondary"
              >
                <IconExternalLink /> Original
              </a>
              <button class="btn btn-sm btn-secondary" onClick={handleRetry} disabled={retrying}>
                <IconRefresh size={14} /> {retrying ? 'Retrying...' : 'Retry'}
              </button>
              <button class="btn btn-sm btn-danger" onClick={handleDelete} disabled={deleting}>
                <IconTrash size={14} /> {deleting ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
          {showContinueReading && (
            <div class="continue-reading-nudge">
              <span>
                You were {Math.round(parseFloat(article.scroll_position) * 100)}% through this
                article.
              </span>
              <button class="btn btn-sm btn-primary" onClick={handleScrollToPosition}>
                Continue reading
              </button>
              <button
                class="btn btn-sm btn-secondary"
                onClick={() => setShowContinueReading(false)}
              >
                Start from top
              </button>
            </div>
          )}
        </div>
        <div
          style={getReaderStyle(readerPrefs.value)}
          data-reader-theme={readerPrefs.value.theme || 'auto'}
          class="reader-body"
        >
          <ReaderToolbar />
          <div class="reader-layout">
            {/* Margin sidenotes — visible on desktop, hidden on mobile */}
            <aside class="reader-sidenotes" aria-label="Article metadata">
              {article.domain && (
                <div class="sidenote">
                  <a href={article.original_url} target="_blank" rel="noopener noreferrer">
                    {article.domain}
                  </a>
                </div>
              )}
              {article.reading_time_minutes && (
                <div class="sidenote">{article.reading_time_minutes} min read</div>
              )}
              {article.word_count && (
                <div class="sidenote">{article.word_count.toLocaleString()} words</div>
              )}
              {article.created_at && (
                <div class="sidenote">saved {formatDate(article.created_at)}</div>
              )}
              {article._cachedAt && (
                <div class="sidenote cached-indicator">
                  <IconInkDrop size={9} /> cached {formatDate(article._cachedAt)}
                </div>
              )}
              {/* Breath marks — tick marks at previous reading pauses */}
              {breathMarks.length > 0 && (
                <div
                  class="sidenote breath-marks-label"
                  title="Tick marks showing where you previously paused reading"
                >
                  breath marks
                </div>
              )}
              {breathMarks.length > 0 &&
                breathMarks.map(function (pos, i) {
                  var opacity = 0.12 + ((i + 1) / breathMarks.length) * 0.22;
                  return (
                    <div
                      key={i}
                      class="breath-mark"
                      style={{ top: pos * 100 + '%', opacity: opacity }}
                      title={'Previous reading pause at ' + Math.round(pos * 100) + '%'}
                    />
                  );
                })}
            </aside>
            <div class="reader-main">
              {(readerPrefs.value.contentMode === 'markdown' ||
                readerPrefs.value.contentMode === 'source') &&
              markdownLoading ? (
                <div class="reader-content">
                  <LoadingSpinner />
                </div>
              ) : readerPrefs.value.contentMode === 'source' ? (
                <>
                  {markdownRaw && (
                    <div class="reader-source-actions">
                      <button
                        class="btn btn-sm btn-secondary"
                        onClick={function () {
                          navigator.clipboard
                            .writeText(markdownRaw)
                            .then(function () {
                              setCopied(true);
                              addToast('Markdown copied to clipboard', 'success');
                              setTimeout(function () {
                                setCopied(false);
                              }, 2000);
                            })
                            .catch(function () {
                              addToast(
                                'Could not copy — your browser may not support clipboard access',
                                'error',
                              );
                            });
                        }}
                      >
                        {copied ? 'Copied' : 'Copy Markdown'}
                      </button>
                    </div>
                  )}
                  <pre class="markdown-view-content">{markdownRaw || 'No markdown available.'}</pre>
                </>
              ) : (
                <article
                  ref={contentRef}
                  class="reader-content"
                  dangerouslySetInnerHTML={{
                    __html:
                      readerPrefs.value.contentMode === 'markdown' && markdownHtml
                        ? markdownHtml
                        : contentHtml,
                  }}
                />
              )}
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
