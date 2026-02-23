import { articles, isOffline, addToast } from './state.js';
import { updateArticle, deleteArticle as apiDeleteArticle, queueOfflineMutation } from './api.js';

export async function optimisticUpdate(articleId, updates, successMsg) {
  try {
    await updateArticle(articleId, updates);
    articles.value = articles.value.map(function (a) {
      return a.id === articleId ? Object.assign({}, a, updates) : a;
    });
    if (successMsg) addToast(successMsg, 'success');
  } catch (err) {
    if (isOffline.value) {
      queueOfflineMutation('/api/articles/' + articleId, 'PATCH', updates);
      articles.value = articles.value.map(function (a) {
        return a.id === articleId ? Object.assign({}, a, updates) : a;
      });
      addToast('Queued for sync', 'info');
    } else {
      addToast(err.message, 'error');
    }
  }
}

export function toggleArchive(article) {
  var newStatus = article.reading_status === 'archived' ? 'unread' : 'archived';
  return optimisticUpdate(
    article.id,
    { reading_status: newStatus },
    newStatus === 'archived' ? 'Archived' : 'Moved to unread'
  );
}

export function toggleFavorite(article) {
  var newFav = !article.is_favorite;
  return optimisticUpdate(article.id, { is_favorite: newFav ? 1 : 0 });
}

export async function removeArticle(articleId) {
  if (!confirm('Delete this article?')) return false;
  try {
    await apiDeleteArticle(articleId);
    articles.value = articles.value.filter(function (a) { return a.id !== articleId; });
    addToast('Article deleted', 'success');
    return true;
  } catch (err) {
    addToast(err.message, 'error');
    return false;
  }
}
