import { useState, useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { tags as tagsSignal, addToast } from '../state.js';
import {
  listTags,
  createTag as apiCreateTag,
  deleteTag as apiDeleteTag,
} from '../api.js';

export function Tags() {
  const [tagName, setTagName] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const tagList = tagsSignal.value;

  useEffect(() => {
    loadTags();
  }, []);

  async function loadTags() {
    setIsLoading(true);
    try {
      tagsSignal.value = await listTags();
    } catch (e) {
      addToast('Failed to load tags: ' + e.message, 'error');
    } finally {
      setIsLoading(false);
    }
  }

  async function handleCreateTag() {
    const name = tagName.trim();
    if (!name) {
      addToast('Enter a tag name', 'error');
      return;
    }
    try {
      const tag = await apiCreateTag(name);
      const newTags = [...tagsSignal.value, tag];
      newTags.sort(function (a, b) { return a.name.localeCompare(b.name); });
      tagsSignal.value = newTags;
      setTagName('');
      addToast('Tag created', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  async function handleDeleteTag(tagId) {
    if (!confirm('Delete this tag?')) return;
    try {
      await apiDeleteTag(tagId);
      tagsSignal.value = tagsSignal.value.filter(function (t) { return t.id !== tagId; });
      addToast('Tag deleted', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') handleCreateTag();
  }

  return (
    <>
      <Header />
      <main class="main-content">
        <h2 class="section-title">Tags</h2>
        <div class="input-group" style={{ marginBottom: '16px' }}>
          <input
            class="input"
            type="text"
            placeholder="New tag name..."
            value={tagName}
            onInput={function (e) { setTagName(e.target.value); }}
            onKeyDown={handleKeyDown}
          />
          <button class="btn btn-primary" onClick={handleCreateTag}>
            Create Tag
          </button>
        </div>

        {isLoading && (
          <div class="loading">
            <div class="spinner"></div>
          </div>
        )}

        <div class="tags-list">
          {!isLoading && tagList.length === 0 && (
            <div class="empty-state">
              <div class="empty-state-title">No tags yet</div>
              <div class="empty-state-text">Create a tag to organize your articles.</div>
            </div>
          )}
          {tagList.map(function (t) {
            return (
              <div class="tag-row" key={t.id}>
                <a
                  href={'#/?tag=' + encodeURIComponent(t.id)}
                  class="tag-row-name"
                >
                  {t.name}
                </a>
                <div class="tag-row-actions">
                  <button
                    class="btn btn-sm btn-danger"
                    onClick={function () { handleDeleteTag(t.id); }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </main>
    </>
  );
}
