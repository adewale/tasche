import { useState, useEffect } from 'preact/hooks';
import { formatDate } from '../utils.js';
import { addToast, isOffline, articles } from '../state.js';
import { updateArticle, deleteArticle as apiDeleteArticle, getArticleTags, listenLater as apiListenLater, queueOfflineMutation, isOfflineCached } from '../api.js';
import { playAudio } from './AudioPlayer.jsx';
import { IconStar, IconTrash, IconCheck, IconCheckSquare, IconHeadphones, IconPlay, IconClock, IconArchive, IconMarkdown } from './Icons.jsx';

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
    window.location.hash = '#/article/' + a.id;
  }

  async function handleFavorite(e) {
    e.stopPropagation();
    const newFav = !a.is_favorite;
    try {
      await updateArticle(a.id, { is_favorite: newFav });
      articles.value = articles.value.map(function (art) {
        return art.id === a.id ? { ...art, is_favorite: newFav ? 1 : 0 } : art;
      });
    } catch (err) {
      if (isOffline.value) {
        queueOfflineMutation('/api/articles/' + a.id, 'PATCH', { is_favorite: newFav });
        articles.value = articles.value.map(function (art) {
          return art.id === a.id ? { ...art, is_favorite: newFav ? 1 : 0 } : art;
        });
        addToast('Queued for sync', 'info');
      } else {
        addToast(err.message, 'error');
      }
    }
  }

  async function handleDelete(e) {
    e.stopPropagation();
    if (!confirm('Delete this article?')) return;
    try {
      await apiDeleteArticle(a.id);
      articles.value = articles.value.filter(function (art) { return art.id !== a.id; });
      if (onDelete) onDelete(a.id);
      addToast('Article deleted', 'success');
    } catch (err) {
      addToast(err.message, 'error');
    }
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

  async function handleArchiveToggle(e) {
    e.stopPropagation();
    var newStatus = a.reading_status === 'archived' ? 'unread' : 'archived';
    try {
      await updateArticle(a.id, { reading_status: newStatus });
      articles.value = articles.value.map(function (art) {
        return art.id === a.id ? { ...art, reading_status: newStatus } : art;
      });
      addToast(newStatus === 'archived' ? 'Archived' : 'Moved to unread', 'success');
    } catch (err) {
      if (isOffline.value) {
        queueOfflineMutation('/api/articles/' + a.id, 'PATCH', { reading_status: newStatus });
        articles.value = articles.value.map(function (art) {
          return art.id === a.id ? { ...art, reading_status: newStatus } : art;
        });
        addToast('Queued for sync', 'info');
      } else {
        addToast(err.message, 'error');
      }
    }
  }

  function handleTagClick(e, tagId) {
    e.stopPropagation();
    e.preventDefault();
    window.location.hash = '#/?tag=' + tagId;
  }

  function handleMarkdown(e) {
    e.stopPropagation();
    window.location.hash = '#/article/' + a.id + '/markdown';
  }

  var audioStatus = a.audio_status;
  var hasAudio = audioStatus === 'ready';
  var audioPending = audioStatus === 'pending' || audioStatus === 'generating';
  var canRequestAudio = !hasAudio && !audioPending;
  var isArchived = a.reading_status === 'archived';

  var thumbnailSrc = a.thumbnail_key ? '/api/articles/' + a.id + '/thumbnail' : null;

  var cardClass = 'article-card';
  if (isProcessing) cardClass += ' article-card--processing';
  if (selectMode) cardClass += ' article-card--selectable';
  if (selected) cardClass += ' article-card--checked';

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
        {thumbnailSrc && !thumbError ? (
          <div class="article-card-thumbnail">
            <img
              src={thumbnailSrc}
              alt=""
              loading="lazy"
              onError={function () { setThumbError(true); }}
            />
          </div>
        ) : (
          <div class="article-card-thumbnail article-card-thumbnail--placeholder">
            <span>{(a.title || a.domain || '?').charAt(0).toUpperCase()}</span>
          </div>
        )}
        <div class="article-card-content">
          <div class="article-card-title">{a.title || a.original_url}</div>
          <div class="article-card-meta">
            {a.domain && (
              <span class="article-card-domain">
                <img
                  class="favicon"
                  src={'https://www.google.com/s2/favicons?domain=' + a.domain + '&sz=16'}
                  alt=""
                  width="14"
                  height="14"
                  loading="lazy"
                  onError={function (e) { e.target.style.display = 'none'; }}
                />
                {a.domain}
              </span>
            )}
            {readingTime && <span>{readingTime}</span>}
            <span>{formatDate(a.created_at)}</span>
            <span class={'reading-status-badge ' + statusClass}>{statusClass}</span>
            {offlineSaved && (
              <span class="offline-indicator" title="Available offline">
                <IconCheck size={10} />
              </span>
            )}
          </div>
          {a.excerpt && <div class="article-card-excerpt">{a.excerpt}</div>}
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
      <div class="article-card-footer">
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
          <button title="View Markdown" onClick={handleMarkdown}>
            <IconMarkdown />
          </button>
          <button
            class={isArchived ? 'archived' : ''}
            title={isArchived ? 'Move to unread' : 'Archive'}
            onClick={handleArchiveToggle}
          >
            <IconArchive filled={isArchived} />
          </button>
          <button
            class={'fav-btn' + (isFav ? ' favorited' : '')}
            title="Toggle favorite"
            onClick={handleFavorite}
          >
            <IconStar filled={!!isFav} />
          </button>
          <button class="delete-btn" title="Delete" onClick={handleDelete}>
            <IconTrash />
          </button>
        </div>
      </div>
    </div>
  );
}
