import { useState, useEffect } from 'preact/hooks';
import { formatDate } from '../utils.js';
import { addToast, isOffline, articles } from '../state.js';
import { updateArticle, deleteArticle as apiDeleteArticle, getArticleTags, queueOfflineMutation, isOfflineCached } from '../api.js';

export function ArticleCard({ article, onDelete }) {
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
    getArticleTags(a.id)
      .then(function (tags) {
        if (!cancelled) setCardTags(tags);
      })
      .catch(function () {
        // Silently ignore tag fetch failures
      });
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
    // Don't navigate if clicking action buttons or tag chips
    if (e.target.closest('.article-card-actions')) return;
    if (e.target.closest('.tag-chip')) return;
    window.location.hash = '#/article/' + a.id;
  }

  async function handleFavorite(e) {
    e.stopPropagation();
    const newFav = !a.is_favorite;
    try {
      await updateArticle(a.id, { is_favorite: newFav });
      // Update the article in the articles signal array
      articles.value = articles.value.map((art) =>
        art.id === a.id ? { ...art, is_favorite: newFav ? 1 : 0 } : art
      );
    } catch (err) {
      if (isOffline.value) {
        queueOfflineMutation('/api/articles/' + a.id, 'PATCH', { is_favorite: newFav });
        articles.value = articles.value.map((art) =>
          art.id === a.id ? { ...art, is_favorite: newFav ? 1 : 0 } : art
        );
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
      articles.value = articles.value.filter((art) => art.id !== a.id);
      if (onDelete) onDelete(a.id);
      addToast('Article deleted', 'success');
    } catch (err) {
      addToast(err.message, 'error');
    }
  }

  function handleTagClick(e, tagId) {
    e.stopPropagation();
    e.preventDefault();
    window.location.hash = '#/?tag=' + tagId;
  }

  var thumbnailSrc = a.thumbnail_key ? '/api/articles/' + a.id + '/thumbnail' : null;

  return (
    <div class={'article-card' + (isProcessing ? ' article-card--processing' : '')} onClick={handleClick}>
      {isProcessing && (
        <div class="processing-overlay">
          <div class="spinner"></div>
          <span class="processing-overlay-text">
            {a.status === 'pending' ? 'Saving...' : 'Processing...'}
          </span>
        </div>
      )}
      <div class="article-card-body">
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
            <span class="article-card-domain">{a.domain || ''}</span>
            {readingTime && <span>{readingTime}</span>}
            <span>{formatDate(a.created_at)}</span>
            <span class={'reading-status-badge ' + statusClass}>{statusClass}</span>
            {offlineSaved && (
              <span class="offline-indicator" title="Available offline">{'\u2713'}</span>
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
          <button
            class={'fav-btn' + (isFav ? ' favorited' : '')}
            title="Toggle favorite"
            onClick={handleFavorite}
          >
            {isFav ? '\u2605' : '\u2606'}
          </button>
          <button class="delete-btn" title="Delete" onClick={handleDelete}>
            {'\uD83D\uDDD1'}
          </button>
        </div>
      </div>
    </div>
  );
}
