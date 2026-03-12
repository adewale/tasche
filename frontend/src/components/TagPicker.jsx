import { useState, useEffect, useRef } from 'preact/hooks';
import { tags as tagsSignal, addToast } from '../state.js';
import {
  listTags,
  createTag as apiCreateTag,
  getArticleTags,
  addArticleTag,
  removeArticleTag,
} from '../api.js';

export function TagPicker({ articleId }) {
  const [articleTags, setArticleTags] = useState([]);
  const [showPicker, setShowPicker] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [highlightIndex, setHighlightIndex] = useState(0);
  const [addingTag, setAddingTag] = useState(false);
  const [removingTagId, setRemovingTagId] = useState(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!articleId) return;
    getArticleTags(articleId)
      .then((tags) => setArticleTags(tags))
      .catch(() => {});
  }, [articleId]);

  // Focus the input when picker opens
  useEffect(() => {
    if (showPicker && inputRef.current) {
      inputRef.current.focus();
    }
  }, [showPicker]);

  // Build filtered suggestions
  const allTags = tagsSignal.value;
  const appliedIds = new Set(articleTags.map((t) => t.id));
  const available = allTags.filter((t) => !appliedIds.has(t.id));
  const trimmed = filterText.trim();
  const filtered = trimmed
    ? available.filter((t) => t.name.toLowerCase().includes(trimmed.toLowerCase()))
    : available;

  // Should we show "Create" option?
  const exactMatch = trimmed
    ? allTags.some((t) => t.name.toLowerCase() === trimmed.toLowerCase())
    : true;
  const showCreate = trimmed && !exactMatch;
  const totalOptions = filtered.length + (showCreate ? 1 : 0);

  // Clamp highlight when list changes
  useEffect(() => {
    if (highlightIndex >= totalOptions) {
      setHighlightIndex(Math.max(0, totalOptions - 1));
    }
  }, [totalOptions, highlightIndex]);

  async function selectTag(tagId, tagName) {
    if (addingTag) return;
    setAddingTag(true);
    try {
      await addArticleTag(articleId, tagId);
      setArticleTags([...articleTags, { id: tagId, name: tagName }]);
      addToast('Tag added', 'success');
      setFilterText('');
      setHighlightIndex(0);
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setAddingTag(false);
    }
  }

  async function createAndApply(name) {
    if (addingTag) return;
    setAddingTag(true);
    try {
      const tag = await apiCreateTag(name);
      // Update global tags signal
      const newTags = [...tagsSignal.value, tag];
      newTags.sort((a, b) => a.name.localeCompare(b.name));
      tagsSignal.value = newTags;
      // Apply to article
      await addArticleTag(articleId, tag.id);
      setArticleTags([...articleTags, { id: tag.id, name: tag.name }]);
      addToast('Tag created and added', 'success');
      setFilterText('');
      setHighlightIndex(0);
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setAddingTag(false);
    }
  }

  async function handleRemoveTag(tagId) {
    if (removingTagId) return;
    setRemovingTagId(tagId);
    try {
      await removeArticleTag(articleId, tagId);
      setArticleTags(articleTags.filter((t) => t.id !== tagId));
      addToast('Tag removed', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setRemovingTagId(null);
    }
  }

  async function openPicker() {
    if (tagsSignal.value.length === 0) {
      try {
        tagsSignal.value = await listTags();
      } catch (_e) {
        // ignore
      }
    }
    setShowPicker(true);
    setFilterText('');
    setHighlightIndex(0);
  }

  function closePicker() {
    setShowPicker(false);
    setFilterText('');
    setHighlightIndex(0);
  }

  function handleKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightIndex((prev) => (prev + 1 < totalOptions ? prev + 1 : prev));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightIndex((prev) => (prev > 0 ? prev - 1 : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (totalOptions === 0) return;
      if (highlightIndex < filtered.length) {
        const tag = filtered[highlightIndex];
        selectTag(tag.id, tag.name);
      } else if (showCreate) {
        createAndApply(trimmed);
      }
    } else if (e.key === 'Escape') {
      closePicker();
    }
  }

  function highlightMatch(name) {
    if (!trimmed) return name;
    const idx = name.toLowerCase().indexOf(trimmed.toLowerCase());
    if (idx === -1) return name;
    return (
      <>
        {name.slice(0, idx)}
        <strong>{name.slice(idx, idx + trimmed.length)}</strong>
        {name.slice(idx + trimmed.length)}
      </>
    );
  }

  return (
    <div>
      <div class="flex-wrap-gap mt-4">
        {articleTags.map((t) => (
          <span
            class={'tag-chip' + (removingTagId === t.id ? ' tag-chip--removing' : '')}
            key={t.id}
          >
            {t.name}
            {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions */}
            <span
              class="tag-chip-remove"
              onClick={() => {
                if (!removingTagId) handleRemoveTag(t.id);
              }}
              style={removingTagId ? { opacity: 0.5, pointerEvents: 'none' } : {}}
            >
              {removingTagId === t.id ? '...' : '\u00D7'}
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
        <div class="tag-picker-autocomplete">
          <input
            ref={inputRef}
            class="input tag-picker-input"
            type="text"
            placeholder="Type to filter or create..."
            value={filterText}
            onInput={(e) => {
              setFilterText(e.target.value);
              setHighlightIndex(0);
            }}
            onKeyDown={handleKeyDown}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
          <div class="tag-picker-dropdown">
            {filtered.map((t, i) => (
              // eslint-disable-next-line jsx-a11y/no-static-element-interactions
              <div
                key={t.id}
                class={
                  'tag-picker-option' + (i === highlightIndex ? ' tag-picker-option--active' : '')
                }
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectTag(t.id, t.name);
                }}
                onMouseEnter={() => setHighlightIndex(i)}
              >
                {highlightMatch(t.name)}
              </div>
            ))}
            {showCreate && (
              // eslint-disable-next-line jsx-a11y/no-static-element-interactions
              <div
                class={
                  'tag-picker-option tag-picker-option--create' +
                  (highlightIndex === filtered.length ? ' tag-picker-option--active' : '')
                }
                onMouseDown={(e) => {
                  e.preventDefault();
                  createAndApply(trimmed);
                }}
                onMouseEnter={() => setHighlightIndex(filtered.length)}
              >
                + Create "{trimmed}"
              </div>
            )}
            {totalOptions === 0 && trimmed && exactMatch && (
              <div class="tag-picker-option tag-picker-option--empty">No matching tags</div>
            )}
          </div>
          <button class="btn btn-sm btn-secondary" onClick={closePicker}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
