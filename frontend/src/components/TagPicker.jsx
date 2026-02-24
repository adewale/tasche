import { useState, useEffect } from 'preact/hooks';
import { tags as tagsSignal, addToast } from '../state.js';
import {
  listTags,
  getArticleTags,
  addArticleTag,
  removeArticleTag,
} from '../api.js';

export function TagPicker({ articleId }) {
  const [articleTags, setArticleTags] = useState([]);
  const [showPicker, setShowPicker] = useState(false);
  const [selectedTagId, setSelectedTagId] = useState('');

  useEffect(() => {
    if (!articleId) return;
    getArticleTags(articleId)
      .then((tags) => setArticleTags(tags))
      .catch(() => {});
  }, [articleId]);

  async function handleAddTag() {
    if (!selectedTagId) {
      addToast('Select a tag', 'error');
      return;
    }
    try {
      await addArticleTag(articleId, selectedTagId);
      const tagName = tagsSignal.value.find((t) => t.id === selectedTagId);
      setArticleTags([
        ...articleTags,
        { id: selectedTagId, name: tagName ? tagName.name : 'Tag' },
      ]);
      addToast('Tag added', 'success');
      setShowPicker(false);
      setSelectedTagId('');
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  async function handleRemoveTag(tagId) {
    try {
      await removeArticleTag(articleId, tagId);
      setArticleTags(articleTags.filter((t) => t.id !== tagId));
      addToast('Tag removed', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  async function openPicker() {
    // Load all tags if not yet loaded
    if (tagsSignal.value.length === 0) {
      try {
        tagsSignal.value = await listTags();
      } catch (e) {
        // ignore
      }
    }
    setShowPicker(true);
  }

  return (
    <div>
      <div class="flex-wrap-gap mt-4">
        {articleTags.map((t) => (
          <span class="tag-chip" key={t.id}>
            {t.name}
            <span class="tag-chip-remove" onClick={() => handleRemoveTag(t.id)}>
              {'\u00D7'}
            </span>
          </span>
        ))}
        {!showPicker && (
          <button class="tag-chip" title="Add tag" onClick={openPicker}>
            + Tag
          </button>
        )}
      </div>
      {showPicker && (
        <div class="tag-picker">
          <select
            class="input tag-picker-select"
            value={selectedTagId}
            onChange={(e) => setSelectedTagId(e.target.value)}
          >
            <option value="">Select a tag...</option>
            {tagsSignal.value
              .filter((t) => !articleTags.some((at) => at.id === t.id))
              .map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <button class="btn btn-sm btn-primary" onClick={handleAddTag}>
            Add
          </button>
          <button
            class="btn btn-sm btn-secondary"
            onClick={() => {
              setShowPicker(false);
              setSelectedTagId('');
            }}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
