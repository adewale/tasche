import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { TagPicker } from '../components/TagPicker.jsx';
import { ReaderToolbar } from '../components/ReaderToolbar.jsx';
import { playAudio, audioState, getAudio } from '../components/AudioPlayer.jsx';
import { articles, addToast } from '../state.js';
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
  IconDownload,
  IconCheck,
  IconCamera,
  IconRefresh,
} from '../components/Icons.jsx';
import {
  getArticle,
  getArticleContent,
  updateArticle,
  listenLater as apiListenLater,
  retryArticle as apiRetryArticle,
  checkOriginal as apiCheckOriginal,
  saveForOffline,
  saveAudioOffline,
  isOfflineCached,
  getAudioTiming,
  getArticleMarkdown,
} from '../api.js';
import DOMPurify from 'dompurify';
import { renderMarkdown } from '../markdown.js';
import { escapeHtml } from '../utils.js';

/**
 * Walk text nodes in the reader content element and wrap sentence boundaries
 * with <span data-sentence-idx="N"> elements for TTS highlight sync.
 */
function wrapSentences(containerEl, sentences) {
  if (!containerEl || !sentences || sentences.length === 0) return;

  var textNodes = [];
  var walker = document.createTreeWalker(containerEl, NodeFilter.SHOW_TEXT, null, false);
  var node;
  while ((node = walker.nextNode())) {
    if (node.textContent.trim()) {
      textNodes.push(node);
    }
  }
  if (textNodes.length === 0) return;

  var fullText = '';
  var nodeMap = [];
  for (var i = 0; i < textNodes.length; i++) {
    var start = fullText.length;
    fullText += textNodes[i].textContent;
    nodeMap.push({ node: textNodes[i], start: start, end: fullText.length });
    if (i < textNodes.length - 1) fullText += ' ';
  }

  var normalizedFull = fullText.replace(/\s+/g, ' ');
  var searchStart = 0;

  for (var si = 0; si < sentences.length; si++) {
    var sentenceText = sentences[si].text;
    if (!sentenceText) continue;

    var needle = sentenceText.substring(0, 60).replace(/\s+/g, ' ').trim();
    if (needle.length === 0) continue;

    var pos = normalizedFull.indexOf(needle, searchStart);
    if (pos === -1) {
      needle = sentenceText.substring(0, 20).replace(/\s+/g, ' ').trim();
      pos = normalizedFull.indexOf(needle, searchStart);
    }
    if (pos === -1) continue;

    var startNodeIdx = -1;
    for (var ni = 0; ni < nodeMap.length; ni++) {
      if (nodeMap[ni].end > pos) {
        startNodeIdx = ni;
        break;
      }
    }
    if (startNodeIdx === -1) continue;

    var wrapper = document.createElement('span');
    wrapper.setAttribute('data-sentence-idx', si);

    var targetNode = nodeMap[startNodeIdx].node;
    var parent = targetNode.parentNode;

    try {
      if (parent && parent !== containerEl) {
        parent.insertBefore(wrapper, targetNode);
      } else {
        containerEl.insertBefore(wrapper, targetNode);
      }
      wrapper.appendChild(targetNode);
    } catch (_e) {
      continue;
    }

    searchStart = pos + needle.length;
  }
}

/**
 * Remove all sentence wrapper spans, restoring the original DOM structure.
 */
function unwrapSentences(containerEl) {
  if (!containerEl) return;
  var spans = containerEl.querySelectorAll('[data-sentence-idx]');
  for (var i = 0; i < spans.length; i++) {
    var span = spans[i];
    var parent = span.parentNode;
    while (span.firstChild) {
      parent.insertBefore(span.firstChild, span);
    }
    parent.removeChild(span);
  }
  containerEl.normalize();
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
  const contentRef = useRef(null);
  const timingRef = useRef(null);
  const prevSentenceRef = useRef(-1);
  const sentenceWrappedRef = useRef(false);

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

    window.addEventListener('scroll', handleScroll);

    return function () {
      if (scrollTimer) {
        clearTimeout(scrollTimer);
      }
      window.removeEventListener('scroll', handleScroll);
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
            addToast('Failed to save for offline', 'error');
          } else if (event.data.what === 'audio') {
            setSavingAudioOffline(false);
            addToast('Failed to download audio', 'error');
          }
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

  // Sentence highlighting during TTS audio playback
  useEffect(
    function () {
      var state = audioState.value;
      // Only activate when this article's audio is playing
      if (!state.visible || state.articleId !== id) {
        // Clean up highlights if audio stopped or switched to another article
        if (sentenceWrappedRef.current && contentRef.current) {
          unwrapSentences(contentRef.current);
          sentenceWrappedRef.current = false;
          prevSentenceRef.current = -1;
          timingRef.current = null;
        }
        return;
      }

      var cancelled = false;

      // Fetch timing data and wrap sentences
      getAudioTiming(id)
        .then(function (timing) {
          if (cancelled || !timing || !timing.sentences || timing.sentences.length === 0) return;
          timingRef.current = timing;

          // Wrap sentences in the reader content
          if (contentRef.current && !sentenceWrappedRef.current) {
            wrapSentences(contentRef.current, timing.sentences);
            sentenceWrappedRef.current = true;
          }
        })
        .catch(function () {
          // Timing data not available -- silently skip highlighting
        });

      var audio = getAudio();

      function onTimeUpdate() {
        var timing = timingRef.current;
        if (!timing || !timing.sentences) return;

        var currentTime = audio.currentTime;

        // Find the current sentence — audio.currentTime is always in the
        // media timeline regardless of playbackRate, so no speed adjustment.
        var idx = -1;
        for (var i = 0; i < timing.sentences.length; i++) {
          var s = timing.sentences[i];
          if (currentTime >= s.start && currentTime < s.end) {
            idx = i;
            break;
          }
        }

        if (idx !== prevSentenceRef.current) {
          // Remove previous highlight
          if (prevSentenceRef.current >= 0 && contentRef.current) {
            var prev = contentRef.current.querySelector(
              '[data-sentence-idx="' + prevSentenceRef.current + '"]',
            );
            if (prev) prev.classList.remove('sentence-active');
          }
          // Add new highlight
          if (idx >= 0 && contentRef.current) {
            var el = contentRef.current.querySelector('[data-sentence-idx="' + idx + '"]');
            if (el) {
              el.classList.add('sentence-active');
              el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
          }
          prevSentenceRef.current = idx;
        }
      }

      function onEnded() {
        // Clear all highlights when audio ends
        if (contentRef.current) {
          var active = contentRef.current.querySelector('.sentence-active');
          if (active) active.classList.remove('sentence-active');
        }
        prevSentenceRef.current = -1;
      }

      audio.addEventListener('timeupdate', onTimeUpdate);
      audio.addEventListener('ended', onEnded);

      return function () {
        cancelled = true;
        audio.removeEventListener('timeupdate', onTimeUpdate);
        audio.removeEventListener('ended', onEnded);
        // Clean up highlights on unmount
        if (sentenceWrappedRef.current && contentRef.current) {
          unwrapSentences(contentRef.current);
          sentenceWrappedRef.current = false;
          prevSentenceRef.current = -1;
          timingRef.current = null;
        }
      };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, audioState.value.articleId, audioState.value.visible, readerPrefs.value.contentMode],
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
    const newFav = !article.is_favorite ? 1 : 0;
    await toggleFavorite(article);
    setArticle({ ...article, is_favorite: newFav });
  }

  async function handleStatusChange(e) {
    try {
      const newStatus = e.target.value;
      await updateArticle(id, { reading_status: newStatus });
      setArticle({ ...article, reading_status: newStatus });
      articles.value = articles.value.map((a) =>
        a.id === id ? { ...a, reading_status: newStatus } : a,
      );
      addToast('Status updated', 'success');
    } catch (err) {
      addToast(err.message, 'error');
    }
  }

  async function handleListenLater() {
    if (listeningLoading) return;
    setListeningLoading(true);
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
    } finally {
      setListeningLoading(false);
    }
  }

  function handlePlayAudio() {
    playAudio(id, article ? article.title : '');
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
        <Header />
        <main class="main-content">
          <LoadingSpinner />
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
    audioRequested || article.audio_status === 'pending' || article.audio_status === 'generating';

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
          </div>
          <TagPicker articleId={id} />
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
          <div class="reader-actions">
            <button
              class={'btn btn-sm ' + (isFav ? 'btn-primary' : 'btn-secondary')}
              onClick={handleFavorite}
            >
              <IconStar filled={!!isFav} size={14} /> {isFav ? 'Favourited' : 'Favourite'}
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
              class={
                'btn btn-sm offline-btn' + (offlineStatus.hasContent ? ' offline-btn--saved' : '')
              }
              onClick={handleSaveOffline}
              disabled={savingOffline}
            >
              {savingOffline ? (
                'Saving...'
              ) : offlineStatus.hasContent ? (
                <>
                  <IconCheck size={14} /> Saved offline
                </>
              ) : (
                <>
                  <IconDownload size={14} /> Save for offline
                </>
              )}
            </button>
            {hasAudio && (
              <button class="btn btn-sm btn-secondary" onClick={handlePlayAudio}>
                <IconPlay size={14} /> Listen
              </button>
            )}
            {hasAudio && (
              <button
                class={
                  'btn btn-sm offline-btn' + (offlineStatus.hasAudio ? ' offline-btn--saved' : '')
                }
                onClick={handleSaveAudioOffline}
                disabled={savingAudioOffline}
              >
                {savingAudioOffline ? (
                  'Downloading...'
                ) : offlineStatus.hasAudio ? (
                  <>
                    <IconCheck size={14} /> Audio offline
                  </>
                ) : (
                  <>
                    <IconDownload size={14} /> Download audio
                  </>
                )}
              </button>
            )}
            {canRequestAudio && (
              <button
                class="btn btn-sm btn-secondary"
                onClick={handleListenLater}
                disabled={listeningLoading}
              >
                {listeningLoading ? (
                  <>
                    <IconClock size={14} /> Requesting...
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
            <button class="btn btn-sm btn-secondary" onClick={handleRetry} disabled={retrying}>
              <IconRefresh size={14} /> {retrying ? 'Retrying...' : 'Retry'}
            </button>
            <button class="btn btn-sm btn-danger" onClick={handleDelete} disabled={deleting}>
              {deleting ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
        <div
          style={getReaderStyle(readerPrefs.value)}
          data-reader-theme={readerPrefs.value.theme || 'auto'}
        >
          <ReaderToolbar />
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
                          addToast('Failed to copy to clipboard', 'error');
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
      </main>
    </>
  );
}
