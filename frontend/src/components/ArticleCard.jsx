import { useState, useEffect } from 'preact/hooks';
import { formatDate } from '../utils.js';
import { addToast, articles, pollAudioStatus, pollArticleStatus } from '../state.js';
import {
  getArticleTags,
  getArticle,
  listenLater as apiListenLater,
  isOfflineCached,
} from '../api.js';
import { toggleArchive, toggleFavorite, removeArticle } from '../articleActions.js';
import { nav } from '../nav.js';
import { playAudio } from './AudioPlayer.jsx';
import {
  IconStar,
  IconTrash,
  IconCheck,
  IconCheckSquare,
  IconHeadphones,
  IconPlay,
  IconClock,
  IconArchive,
} from './Icons.jsx';
import { InkFavicon } from './InkFavicon.jsx';
import { InkWashThumbnail } from './InkWashThumbnail.jsx';

const tagCache = new Map();

export function ArticleCard({ article, selectMode, selected, onToggleSelect }) {
  const a = article;
  const readingTime = a.reading_time_minutes ? a.reading_time_minutes + ' min read' : '';
  const isFav = a.is_favorite;
  const progress = a.reading_progress ? parseFloat(a.reading_progress) : 0;
  const isProcessing = a.status === 'pending' || a.status === 'processing';

  const [cardTags, setCardTags] = useState([]);
  const [offlineSaved, setOfflineSaved] = useState(false);
  const [audioLoading, setAudioLoading] = useState(false);

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
    if (isProcessing) {
      pollArticleStatus(a.id, getArticle);
    }
    return function () {
      cancelled = true;
    };
  }, [a.id, isProcessing]);

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
  var audioPending = audioStatus === 'pending' || audioStatus === 'generating';
  var canRequestAudio = !hasAudio && !audioPending;
  var isArchived = a.reading_status === 'archived';

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
          {!isProcessing && hasAudio && (
            <button class="audio-ready" title="Play audio" onClick={handlePlayAudio}>
              <IconPlay />
            </button>
          )}
          {!isProcessing && audioPending && (
            <button class="audio-pending" title="Generating audio..." disabled>
              <IconClock />
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
