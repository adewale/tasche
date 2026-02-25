import { useState, useEffect, useRef } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { tags as tagsSignal, addToast } from '../state.js';
import {
  listTags,
  createTag as apiCreateTag,
  deleteTag as apiDeleteTag,
  renameTag as apiRenameTag,
  getTagRules,
  createTagRule as apiCreateTagRule,
  deleteTagRule as apiDeleteTagRule,
} from '../api.js';
import { IconPencil } from '../components/Icons.jsx';

var MATCH_TYPE_LABELS = {
  domain: 'Domain',
  title_contains: 'Title Contains',
  url_contains: 'URL Contains',
};

export function Tags() {
  var [tagName, setTagName] = useState('');
  var [isLoading, setIsLoading] = useState(true);
  var [creatingTag, setCreatingTag] = useState(false);
  var [deletingTagId, setDeletingTagId] = useState(null);
  var [renamingTagId, setRenamingTagId] = useState(null);
  var [creatingRule, setCreatingRule] = useState(false);
  var [deletingRuleId, setDeletingRuleId] = useState(null);
  var [editingTagId, setEditingTagId] = useState(null);
  var [editName, setEditName] = useState('');
  var editInputRef = useRef(null);
  var tagList = tagsSignal.value;

  // Tag rules state
  var [rules, setRules] = useState([]);
  var [rulesLoading, setRulesLoading] = useState(true);
  var [ruleTagId, setRuleTagId] = useState('');
  var [ruleMatchType, setRuleMatchType] = useState('domain');
  var [rulePattern, setRulePattern] = useState('');

  useEffect(function () {
    loadTags();
    loadRules();
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
    } catch (e) {
      addToast('Failed to load tags: ' + e.message, 'error');
    } finally {
      setIsLoading(false);
    }
  }

  async function loadRules() {
    setRulesLoading(true);
    try {
      var data = await getTagRules();
      setRules(data);
    } catch (e) {
      addToast('Failed to load tag rules: ' + e.message, 'error');
    } finally {
      setRulesLoading(false);
    }
  }

  async function handleCreateTag() {
    if (creatingTag) return;
    var name = tagName.trim();
    if (!name) {
      addToast('Enter a tag name', 'error');
      return;
    }
    setCreatingTag(true);
    try {
      var tag = await apiCreateTag(name);
      var newTags = [...tagsSignal.value, tag];
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
      // Also remove rules for this tag
      setRules(
        rules.filter(function (r) {
          return r.tag_id !== tagId;
        }),
      );
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
    var trimmed = editName.trim();
    if (!trimmed) {
      addToast('Tag name cannot be empty', 'error');
      return;
    }
    var currentTag = tagsSignal.value.find(function (t) {
      return t.id === tagId;
    });
    if (currentTag && currentTag.name === trimmed) {
      cancelRename();
      return;
    }
    setRenamingTagId(tagId);
    try {
      var updated = await apiRenameTag(tagId, trimmed);
      var newTags = tagsSignal.value.map(function (t) {
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

  async function handleCreateRule() {
    if (creatingRule) return;
    if (!ruleTagId) {
      addToast('Select a tag', 'error');
      return;
    }
    var pattern = rulePattern.trim();
    if (!pattern) {
      addToast('Enter a pattern', 'error');
      return;
    }
    setCreatingRule(true);
    try {
      var rule = await apiCreateTagRule({
        tag_id: ruleTagId,
        match_type: ruleMatchType,
        pattern: pattern,
      });
      setRules([].concat(rules, [rule]));
      setRulePattern('');
      addToast('Rule created', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setCreatingRule(false);
    }
  }

  async function handleDeleteRule(ruleId) {
    if (deletingRuleId) return;
    setDeletingRuleId(ruleId);
    try {
      await apiDeleteTagRule(ruleId);
      setRules(
        rules.filter(function (r) {
          return r.id !== ruleId;
        }),
      );
      addToast('Rule deleted', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setDeletingRuleId(null);
    }
  }

  function handleRuleKeyDown(e) {
    if (e.key === 'Enter') handleCreateRule();
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
            var isEditing = editingTagId === t.id;
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

        {/* Tag Rules Section */}
        <h2 class="section-title mt-10">Tag Rules</h2>
        <p class="tag-rules-description">
          Rules automatically tag new articles based on their domain, title, or URL.
        </p>

        {tagList.length > 0 && (
          <div class="tag-rule-form">
            <select
              class="input tag-rule-select"
              value={ruleTagId}
              onChange={function (e) {
                setRuleTagId(e.target.value);
              }}
            >
              <option value="">Select tag...</option>
              {tagList.map(function (t) {
                return (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                );
              })}
            </select>
            <select
              class="input tag-rule-select"
              value={ruleMatchType}
              onChange={function (e) {
                setRuleMatchType(e.target.value);
              }}
            >
              <option value="domain">Domain</option>
              <option value="title_contains">Title Contains</option>
              <option value="url_contains">URL Contains</option>
            </select>
            <input
              class="input"
              type="text"
              placeholder={ruleMatchType === 'domain' ? 'example.com' : 'pattern...'}
              value={rulePattern}
              onInput={function (e) {
                setRulePattern(e.target.value);
              }}
              onKeyDown={handleRuleKeyDown}
            />
            <button class="btn btn-primary" onClick={handleCreateRule} disabled={creatingRule}>
              {creatingRule ? 'Adding...' : 'Add Rule'}
            </button>
          </div>
        )}

        {rulesLoading && <LoadingSpinner />}

        <div class="tags-list mt-3">
          {!rulesLoading && rules.length === 0 && (
            <EmptyState title="No rules yet">
              Add a rule to automatically tag articles when they are saved.
            </EmptyState>
          )}
          {rules.map(function (r) {
            return (
              <div class="tag-row" key={r.id}>
                <div class="tag-rule-info">
                  <span class="tag-chip">{r.tag_name}</span>
                  <span class="tag-rule-match-type">
                    {MATCH_TYPE_LABELS[r.match_type] || r.match_type}
                  </span>
                  <span class="tag-rule-pattern">{r.pattern}</span>
                </div>
                <div class="tag-row-actions">
                  <button
                    class="btn btn-sm btn-danger"
                    onClick={function () {
                      handleDeleteRule(r.id);
                    }}
                    disabled={deletingRuleId === r.id}
                  >
                    {deletingRuleId === r.id ? 'Deleting...' : 'Delete'}
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
