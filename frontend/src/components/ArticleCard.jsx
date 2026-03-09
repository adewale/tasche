import { useState, useEffect, useCallback } from 'preact/hooks';
import { formatDate } from '../utils.js';
import { addToast, articles, pollAudioStatus, pollArticleStatus } from '../state.js';
import {
  getArticleTags,
  getArticle,
  listenLater as apiListenLater,
  isOfflineCached,
  saveForOffline,
  removeFromOffline,
} from '../api.js';
import { toggleArchive, toggleFavorite, removeArticle } from '../articleActions.js';
import { nav } from '../nav.js';
import { playAudio, audioState } from './AudioPlayer.jsx';
import {
  IconStar,
  IconTrash,
  IconOffline,
  IconCheckSquare,
  IconHeadphones,
  IconPlay,
  IconSoundBars,
  IconClock,
  IconArchive,
  IconRefresh,
} from './Icons.jsx';
import { InkFavicon } from './InkFavicon.jsx';
import { InkWashThumbnail } from './InkWashThumbnail.jsx';
import { useSWMessage } from '../hooks/useSWMessage.js';

const tagCache = new Map();

export function ArticleCard({ article, selectMode, selected, onToggleSelect }) {
  const a = article;
  const readingTime = a.reading_time_minutes ? a.reading_time_minutes + ' min read' : '';
  const isFav = a.is_favorite;
  const progress = a.reading_progress ? parseFloat(a.reading_progress) : 0;
  const isProcessing = a.status === 'pending' || a.status === 'processing';

  const [cardTags, setCardTags] = useState([]);
  const [offlineSaved, setOfflineSaved] = useState(false);
  const [offlineLoading, setOfflineLoading] = useState(false);
  const [audioLoading, setAudioLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (a.tags) {
      // Tags included inline from list endpoint — no extra fetch needed.
      tagCache.set(a.id, a.tags);
      setCardTags(a.tags);
    } else if (tagCache.has(a.id)) {
      setCardTags(tagCache.get(a.id));
    } else {
      getArticleTags(a.id)
        .then(function (tags) {
          tagCache.set(a.id, tags);
          if (!cancelled) setCardTags(tags);
        })
        .catch(function () {});
    }
    isOfflineCached(a.id)
      .then(function (status) {
        if (!cancelled) setOfflineSaved(status.hasContent);
      })
      .catch(function () {});
    if (isProcessing) {
      pollArticleStatus(a.id, getArticle);
    }
    return function () {
      cancelled = true;
    };
  }, [a.id, a.tags, isProcessing]);

  useSWMessage(
    useCallback(
      function (event) {
        if (!event.data || event.data.articleId !== a.id) return;
        if (event.data.type === 'OFFLINE_SAVED' && event.data.what === 'content') {
          setOfflineSaved(true);
          setOfflineLoading(false);
          addToast('Saved for offline reading', 'success');
        }
        if (event.data.type === 'OFFLINE_SAVE_ERROR' && event.data.what === 'content') {
          setOfflineLoading(false);
          addToast('Could not save offline', 'error');
        }
        if (event.data.type === 'OFFLINE_REMOVED') {
          setOfflineSaved(false);
          setOfflineLoading(false);
          addToast('Removed from offline', 'success');
        }
        if (event.data.type === 'OFFLINE_REMOVE_ERROR') {
          setOfflineLoading(false);
          addToast('Could not remove offline cache', 'error');
        }
      },
      [a.id],
    ),
  );

  function handleOfflineToggle(e) {
    e.stopPropagation();
    if (offlineLoading) return;
    setOfflineLoading(true);
    if (offlineSaved) {
      removeFromOffline(a.id);
    } else {
      saveForOffline(a.id);
    }
  }

  function handleClick(e) {
    if (selectMode) {
      e.preventDefault();
      if (onToggleSelect) onToggleSelect(a.id);
      return;
    }
    if (e.target.closest('.article-card-actions')) return;
    if (e.target.closest('.tag-chip')) return;
    nav.article(a.id);
  }

  function handleFavorite(e) {
    e.stopPropagation();
    toggleFavorite(a);
  }

  function handleDelete(e) {
    e.stopPropagation();
    removeArticle(a.id);
  }

  async function handleListenLater(e) {
    e.stopPropagation();
    if (audioLoading) return;
    setAudioLoading(true);
    try {
      await apiListenLater(a.id);
      articles.value = articles.value.map(function (art) {
        return art.id === a.id ? { ...art, audio_status: 'pending' } : art;
      });
      addToast('Audio generation queued', 'success');
      pollAudioStatus(a.id, getArticle);
    } catch (err) {
      if (err.status === 409) {
        addToast('Audio generation is already in progress', 'info');
      } else {
        addToast(err.message, 'error');
      }
    } finally {
      setAudioLoading(false);
    }
  }

  function handlePlayAudio(e) {
    e.stopPropagation();
    playAudio(a.id, a.title || '', a.domain || '', a.thumbnail_key);
  }

  function handleArchiveToggle(e) {
    e.stopPropagation();
    toggleArchive(a);
  }

  function handleTagClick(e, tagId) {
    e.stopPropagation();
    e.preventDefault();
    nav.tagFilter(tagId);
  }

  var audioStatus = a.audio_status;
  var hasAudio = audioStatus === 'ready';
  var audioPending = audioStatus === 'pending';
  var audioStuck = audioStatus === 'generating';
  var audioFailed = audioStatus === 'failed';
  var canRequestAudio = !hasAudio && !audioPending && !audioStuck && !audioFailed;
  var isArchived = a.reading_status === 'archived';
  var isThisPlaying = audioState.value.articleId === a.id && audioState.value.isPlaying;

  var thumbnailSrc = a.thumbnail_key ? '/api/articles/' + a.id + '/thumbnail' : null;
  var hasThumbnail = !!thumbnailSrc;

  var cardClass = 'article-card';
  if (!hasThumbnail) cardClass += ' article-card--compact';
  if (isProcessing) cardClass += ' article-card--processing';
  if (selectMode) cardClass += ' article-card--selectable';
  if (selected) cardClass += ' article-card--checked';

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div class={cardClass} onClick={handleClick}>
      {isProcessing && (
        <svg class="processing-march" width="100%" height="100%">
          <rect x="1" y="1" width="calc(100% - 2px)" height="calc(100% - 2px)" rx="2" />
        </svg>
      )}
      <div class="article-card-body">
        {selectMode && (
          <div class="article-card-checkbox">
            <IconCheckSquare size={20} checked={!!selected} />
          </div>
        )}
        {hasThumbnail ? (
          <div class="article-card-thumbnail">
            <InkWashThumbnail src={thumbnailSrc} alt="" />
          </div>
        ) : a.domain ? (
          <div class="article-card-favicon">
            <div class="favicon-container">
              <InkFavicon domain={a.domain} size={16} />
            </div>
          </div>
        ) : null}
        <div class="article-card-content">
          <div class="article-card-title">{a.title || a.original_url}</div>
          <div class="article-card-meta">
            {a.domain && <span>{a.domain}</span>}
            {readingTime && <span>{readingTime}</span>}
            <span>{formatDate(a.created_at)}</span>
          </div>
          {a.excerpt && <div class="article-card-excerpt">{a.excerpt}</div>}
        </div>
      </div>
      <div class="article-card-footer">
        {cardTags.length > 0 && (
          <div class="article-card-tags">
            {cardTags.map(function (tag) {
              return (
                <a
                  key={tag.id}
                  href={'#/?tag=' + tag.id}
                  class="tag-chip"
                  onClick={function (e) {
                    handleTagClick(e, tag.id);
                  }}
                >
                  {tag.name}
                </a>
              );
            })}
          </div>
        )}
        <div class="article-card-actions">
          {!isProcessing && hasAudio && isThisPlaying && (
            <button class="audio-playing" title="Now playing" disabled>
              <IconSoundBars />
            </button>
          )}
          {!isProcessing && hasAudio && !isThisPlaying && (
            <button class="audio-ready" title="Play audio" onClick={handlePlayAudio}>
              <IconPlay />
            </button>
          )}
          {!isProcessing && audioPending && (
            <button class="audio-pending" title="Generating audio..." disabled>
              <IconClock />
            </button>
          )}
          {!isProcessing && !audioPending && (hasAudio || audioFailed || audioStuck) && (
            <button title="Regenerate audio" onClick={handleListenLater} disabled={audioLoading}>
              {audioLoading ? <IconClock /> : <IconRefresh />}
            </button>
          )}
          {!isProcessing && canRequestAudio && (
            <button title="Listen later" onClick={handleListenLater} disabled={audioLoading}>
              {audioLoading ? <IconClock /> : <IconHeadphones />}
            </button>
          )}
          {!isProcessing && (
            <button
              class={isArchived ? 'archived' : ''}
              title={isArchived ? 'Move to unread' : 'Archive'}
              onClick={handleArchiveToggle}
            >
              <IconArchive filled={isArchived} />
            </button>
          )}
          {!isProcessing && (
            <button
              class={'fav-btn' + (isFav ? ' favorited' : '')}
              title="Toggle favourite"
              onClick={handleFavorite}
            >
              <IconStar filled={!!isFav} />
            </button>
          )}
          {!isProcessing && (
            <button
              class="offline-btn"
              title={offlineSaved ? 'Remove offline copy' : 'Save for offline'}
              onClick={handleOfflineToggle}
              disabled={offlineLoading}
            >
              {offlineLoading ? <IconClock /> : <IconOffline filled={offlineSaved} />}
            </button>
          )}
          {!isProcessing && (
            <button class="delete-btn" title="Delete" onClick={handleDelete}>
              <IconTrash />
            </button>
          )}
        </div>
      </div>
      {progress > 0 && (
        <div class="reading-progress-bar">
          <div
            class="reading-progress-bar-fill"
            style={{ width: Math.round(progress * 100) + '%' }}
          />
        </div>
      )}
    </div>
  );
}
