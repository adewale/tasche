import { useState, useEffect, useRef } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { tags as tagsSignal, addToast } from '../state.js';
import {
  listTags,
  createTag as apiCreateTag,
  deleteTag as apiDeleteTag,
  renameTag as apiRenameTag,
} from '../api.js';
import { IconPencil } from '../components/Icons.jsx';

export function Tags() {
  const [tagName, setTagName] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [creatingTag, setCreatingTag] = useState(false);
  const [deletingTagId, setDeletingTagId] = useState(null);
  const [renamingTagId, setRenamingTagId] = useState(null);
  const [editingTagId, setEditingTagId] = useState(null);
  const [editName, setEditName] = useState('');
  const editInputRef = useRef(null);
  const tagList = tagsSignal.value;

  useEffect(function () {
    loadTags();
  }, []);

  useEffect(
    function () {
      if (editingTagId && editInputRef.current) {
        editInputRef.current.focus();
        editInputRef.current.select();
      }
    },
    [editingTagId],
  );

  async function loadTags() {
    setIsLoading(true);
    try {
      tagsSignal.value = await listTags();
    } catch (_e) {
      addToast('Could not load tags. Try refreshing the page.', 'error');
    } finally {
      setIsLoading(false);
    }
  }

  async function handleCreateTag() {
    if (creatingTag) return;
    const name = tagName.trim();
    if (!name) {
      addToast('Enter a tag name', 'error');
      return;
    }
    setCreatingTag(true);
    try {
      const tag = await apiCreateTag(name);
      const newTags = [...tagsSignal.value, tag];
      newTags.sort(function (a, b) {
        return a.name.localeCompare(b.name);
      });
      tagsSignal.value = newTags;
      setTagName('');
      addToast('Tag created', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setCreatingTag(false);
    }
  }

  async function handleDeleteTag(tagId) {
    if (deletingTagId) return;
    if (!confirm('Delete this tag?')) return;
    setDeletingTagId(tagId);
    try {
      await apiDeleteTag(tagId);
      tagsSignal.value = tagsSignal.value.filter(function (t) {
        return t.id !== tagId;
      });
      addToast('Tag deleted', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setDeletingTagId(null);
    }
  }

  function startRename(tag) {
    setEditingTagId(tag.id);
    setEditName(tag.name);
  }

  function cancelRename() {
    setEditingTagId(null);
    setEditName('');
  }

  async function saveRename(tagId) {
    if (renamingTagId) return;
    const trimmed = editName.trim();
    if (!trimmed) {
      addToast('Tag name cannot be empty', 'error');
      return;
    }
    const currentTag = tagsSignal.value.find(function (t) {
      return t.id === tagId;
    });
    if (currentTag && currentTag.name === trimmed) {
      cancelRename();
      return;
    }
    setRenamingTagId(tagId);
    try {
      const updated = await apiRenameTag(tagId, trimmed);
      const newTags = tagsSignal.value.map(function (t) {
        if (t.id === tagId) {
          return Object.assign({}, t, { name: updated.name });
        }
        return t;
      });
      newTags.sort(function (a, b) {
        return a.name.localeCompare(b.name);
      });
      tagsSignal.value = newTags;
      setEditingTagId(null);
      setEditName('');
      addToast('Tag renamed', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setRenamingTagId(null);
    }
  }

  function handleEditKeyDown(e, tagId) {
    if (e.key === 'Enter') {
      e.preventDefault();
      saveRename(tagId);
    } else if (e.key === 'Escape') {
      cancelRename();
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
        <div class="input-group mb-4">
          <input
            class="input"
            type="text"
            placeholder="New tag name..."
            value={tagName}
            onInput={function (e) {
              setTagName(e.target.value);
            }}
            onKeyDown={handleKeyDown}
          />
          <button class="btn btn-primary" onClick={handleCreateTag} disabled={creatingTag}>
            {creatingTag ? 'Creating...' : 'Create Tag'}
          </button>
        </div>

        {isLoading && <LoadingSpinner />}

        <div class="tags-list">
          {!isLoading && tagList.length === 0 && (
            <EmptyState title="No tags yet">Create a tag to organise your articles.</EmptyState>
          )}
          {tagList.map(function (t) {
            const isEditing = editingTagId === t.id;
            return (
              <div class="tag-row" key={t.id}>
                {isEditing ? (
                  <div class="tag-row-edit">
                    <input
                      ref={editInputRef}
                      class="input tag-row-edit-input"
                      type="text"
                      value={editName}
                      onInput={function (e) {
                        setEditName(e.target.value);
                      }}
                      onKeyDown={function (e) {
                        handleEditKeyDown(e, t.id);
                      }}
                      onBlur={function () {
                        cancelRename();
                      }}
                    />
                  </div>
                ) : (
                  <a href={'#/?tag=' + encodeURIComponent(t.id)} class="tag-row-name">
                    {t.name}
                    <span class="tag-row-count">
                      {t.article_count === 1 ? '1 article' : (t.article_count || 0) + ' articles'}
                    </span>
                  </a>
                )}
                <div class="tag-row-actions">
                  {isEditing ? (
                    <>
                      <button
                        class="btn btn-sm btn-primary"
                        onMouseDown={function (e) {
                          e.preventDefault();
                          saveRename(t.id);
                        }}
                        disabled={renamingTagId === t.id}
                      >
                        {renamingTagId === t.id ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        class="btn btn-sm btn-secondary"
                        onMouseDown={function (e) {
                          e.preventDefault();
                          cancelRename();
                        }}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        class="btn btn-sm btn-secondary"
                        onClick={function () {
                          startRename(t);
                        }}
                        title="Rename tag"
                      >
                        <IconPencil size={14} />
                      </button>
                      <button
                        class="btn btn-sm btn-danger"
                        onClick={function () {
                          handleDeleteTag(t.id);
                        }}
                        disabled={deletingTagId === t.id}
                      >
                        {deletingTagId === t.id ? 'Deleting...' : 'Delete'}
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </main>
    </>
  );
}
