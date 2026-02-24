import { useState, useEffect } from 'preact/hooks';
import { formatDate } from '../utils.js';
import { addToast, articles } from '../state.js';
import { getArticleTags, listenLater as apiListenLater, isOfflineCached } from '../api.js';
import { toggleArchive, toggleFavorite, removeArticle } from '../articleActions.js';
import { nav } from '../nav.js';
import { playAudio } from './AudioPlayer.jsx';
import { IconStar, IconTrash, IconCheck, IconCheckSquare, IconHeadphones, IconPlay, IconClock, IconArchive } from './Icons.jsx';

const tagCache = new Map();

export function ArticleCard({ article, onDelete, selectMode, selected, onToggleSelect }) {
  const a = article;
  const readingTime = a.reading_time_minutes ? a.reading_time_minutes + ' min read' : '';
  const statusClass = a.reading_status || 'unread';
  const isFav = a.is_favorite;
  const progress = a.reading_progress ? parseFloat(a.reading_progress) : 0;
  const isProcessing = a.status === 'pending' || a.status === 'processing';

  const [cardTags, setCardTags] = useState([]);
  const [thumbError, setThumbError] = useState(false);
  const [offlineSaved, setOfflineSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (tagCache.has(a.id)) {
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
    return function () {
      cancelled = true;
    };
  }, [a.id]);

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
    removeArticle(a.id).then(function (deleted) {
      if (deleted && onDelete) onDelete(a.id);
    });
  }

  async function handleListenLater(e) {
    e.stopPropagation();
    try {
      await apiListenLater(a.id);
      articles.value = articles.value.map(function (art) {
        return art.id === a.id ? { ...art, audio_status: 'pending' } : art;
      });
      addToast('Audio generation queued', 'success');
    } catch (err) {
      if (err.status === 409) {
        addToast('Audio generation is already in progress', 'info');
      } else {
        addToast(err.message, 'error');
      }
    }
  }

  function handlePlayAudio(e) {
    e.stopPropagation();
    playAudio(a.id, a.title || '');
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
  var audioPending = audioStatus === 'pending' || audioStatus === 'generating';
  var canRequestAudio = !hasAudio && !audioPending;
  var isArchived = a.reading_status === 'archived';

  var thumbnailSrc = a.thumbnail_key ? '/api/articles/' + a.id + '/thumbnail' : null;
  var hasThumbnail = thumbnailSrc && !thumbError;
  var faviconSrc = a.domain ? 'https://www.google.com/s2/favicons?domain=' + a.domain + '&sz=32' : null;

  var cardClass = 'article-card';
  if (!hasThumbnail) cardClass += ' article-card--compact';
  if (isProcessing) cardClass += ' article-card--processing';
  if (selectMode) cardClass += ' article-card--selectable';
  if (selected) cardClass += ' article-card--checked';
  if (statusClass === 'reading') cardClass += ' article-card--reading';

  return (
    <div class={cardClass} onClick={handleClick}>
      {isProcessing && (
        <div class="processing-overlay">
          <div class="spinner"></div>
          <span class="processing-overlay-text">
            {a.status === 'pending' ? 'Saving...' : 'Processing...'}
          </span>
        </div>
      )}
      <div class="article-card-body">
        {selectMode && (
          <div class="article-card-checkbox">
            <IconCheckSquare size={20} checked={!!selected} />
          </div>
        )}
        {hasThumbnail ? (
          <div class="article-card-thumbnail">
            <img
              src={thumbnailSrc}
              alt=""
              loading="lazy"
              onError={function () { setThumbError(true); }}
            />
          </div>
        ) : faviconSrc ? (
          <div class="article-card-favicon">
            <div class="favicon-container">
              <img
                src={faviconSrc}
                alt=""
                width="16"
                height="16"
                loading="lazy"
                onError={function (e) { e.target.closest('.article-card-favicon').style.display = 'none'; }}
              />
            </div>
          </div>
        ) : null}
        <div class="article-card-content">
          <div class="article-card-title">{a.title || a.original_url}</div>
          <div class="article-card-meta">
            {a.domain && <span>{a.domain}</span>}
            {readingTime && <span>{readingTime}</span>}
            <span>{formatDate(a.created_at)}</span>
            {offlineSaved && (
              <span class="offline-indicator" title="Available offline">
                <IconCheck size={10} />
              </span>
            )}
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
                  onClick={function (e) { handleTagClick(e, tag.id); }}
                >
                  {tag.name}
                </a>
              );
            })}
          </div>
        )}
        <div class="article-card-actions">
          {hasAudio && (
            <button class="audio-ready" title="Play audio" onClick={handlePlayAudio}>
              <IconPlay />
            </button>
          )}
          {audioPending && (
            <button class="audio-pending" title="Generating audio..." disabled>
              <IconClock />
            </button>
          )}
          {canRequestAudio && (
            <button title="Listen later" onClick={handleListenLater}>
              <IconHeadphones />
            </button>
          )}
          <button
            class={isArchived ? 'archived' : ''}
            title={isArchived ? 'Move to unread' : 'Archive'}
            onClick={handleArchiveToggle}
          >
            <IconArchive filled={isArchived} />
          </button>
          <button
            class={'fav-btn' + (isFav ? ' favorited' : '')}
            title="Toggle favourite"
            onClick={handleFavorite}
          >
            <IconStar filled={!!isFav} />
          </button>
          <button class="delete-btn" title="Delete" onClick={handleDelete}>
            <IconTrash />
          </button>
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
