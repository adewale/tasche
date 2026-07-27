// Bookmarklet popup logic — external file so the strict CSP
// (script-src 'self', no 'unsafe-inline') applies to /bookmarklet.
(function () {
  var params = new URLSearchParams(window.location.search);
  var pageUrl = params.get('url');
  var pageTitle = params.get('title') || '';
  var statusEl = document.getElementById('status');

  if (!pageUrl) {
    statusEl.className = 'status error';
    statusEl.textContent = 'No URL provided.';
    return;
  }

  // Pre-fill title and URL display
  var titleInput = document.getElementById('title-input');
  titleInput.value = pageTitle.length > 500 ? pageTitle.slice(0, 500) : pageTitle;

  var urlDisplay = document.getElementById('url-display');
  try {
    var u = new URL(pageUrl);
    urlDisplay.innerHTML = '<strong>' + esc(u.hostname) + '</strong>' + esc(u.pathname + u.search).slice(0, 60);
  } catch (_e) {
    urlDisplay.textContent = pageUrl.slice(0, 80);
  }

  // State
  var allTags = [];       // [{id, name, article_count}, ...]
  var allRules = [];      // [{tag_id, match_type, pattern}, ...]
  var selectedTagIds = []; // tag IDs the user has chosen
  var saving = false;

  // Elements
  var tagInput = document.getElementById('tag-input');
  var datalistEl = document.getElementById('tag-options');
  var selectedTagsEl = document.getElementById('selected-tags');
  var suggestionsEl = document.getElementById('suggestions');
  var suggestionChipsEl = document.getElementById('suggestion-chips');
  var btnSave = document.getElementById('btn-save');
  var btnAudio = document.getElementById('btn-audio');

  // Focus tags input
  tagInput.focus();

  // Fetch tags and rules in parallel
  var tagsReady = fetch('/api/tags', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (tags) { allTags = tags; renderDatalist(); renderSuggestions(); })
    .catch(function () {});

  fetch('/api/tag-rules', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (rules) { allRules = rules; return tagsReady; })
    .then(function () { renderSuggestions(); })
    .catch(function () {});

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function renderDatalist() {
    datalistEl.innerHTML = '';
    allTags.forEach(function (t) {
      if (selectedTagIds.indexOf(t.id) !== -1) return;
      var opt = document.createElement('option');
      opt.value = t.name;
      opt.textContent = t.name + ' (' + (t.article_count || 0) + ')';
      datalistEl.appendChild(opt);
    });
  }

  function renderSelectedTags() {
    selectedTagsEl.innerHTML = '';
    selectedTagIds.forEach(function (id) {
      var tag = allTags.find(function (t) { return t.id === id; });
      if (!tag) return;
      var chip = document.createElement('span');
      chip.className = 'tag-chip';
      chip.textContent = tag.name;
      var rm = document.createElement('span');
      rm.className = 'tag-chip-remove';
      rm.textContent = '\u00D7';
      rm.onclick = function () { removeTag(id); };
      chip.appendChild(rm);
      selectedTagsEl.appendChild(chip);
    });
    renderDatalist();
    renderSuggestions();
  }

  function addTag(id) {
    if (selectedTagIds.indexOf(id) === -1) {
      selectedTagIds.push(id);
      renderSelectedTags();
    }
    tagInput.value = '';
    tagInput.focus();
  }

  function removeTag(id) {
    selectedTagIds = selectedTagIds.filter(function (x) { return x !== id; });
    renderSelectedTags();
    tagInput.focus();
  }

  function matchTagByName(name) {
    var lower = name.toLowerCase().trim();
    if (!lower) return null;
    return allTags.find(function (t) { return t.name.toLowerCase() === lower; }) || null;
  }

  // Tag rule matching — mirrors apply_auto_tags() in processing.py
  function getMatchedRuleTagIds() {
    var matched = {};
    var domain = '';
    try { domain = new URL(pageUrl).hostname.toLowerCase(); } catch (_e) {}
    var titleLower = pageTitle.toLowerCase();
    var urlLower = pageUrl.toLowerCase();
    allRules.forEach(function (rule) {
      var p = (rule.pattern || '').toLowerCase();
      if (!p || !rule.tag_id) return;
      if (rule.match_type === 'domain' && domain === p) matched[rule.tag_id] = true;
      if (rule.match_type === 'title_contains' && titleLower.indexOf(p) !== -1) matched[rule.tag_id] = true;
      if (rule.match_type === 'url_contains' && urlLower.indexOf(p) !== -1) matched[rule.tag_id] = true;
    });
    return matched;
  }

  function renderSuggestions() {
    var matchedIds = getMatchedRuleTagIds();
    // Frequent tags: top by article_count, excluding rule-matched
    var frequent = allTags
      .filter(function (t) { return !matchedIds[t.id]; })
      .sort(function (a, b) { return (b.article_count || 0) - (a.article_count || 0); })
      .slice(0, 8);

    var ruleMatched = allTags.filter(function (t) { return matchedIds[t.id]; });

    if (ruleMatched.length === 0 && frequent.length === 0) {
      suggestionsEl.style.display = 'none';
      return;
    }
    suggestionsEl.style.display = '';
    suggestionChipsEl.innerHTML = '';

    function makeChip(tag, isRule) {
      var chip = document.createElement('span');
      chip.className = 'tag-chip suggested' + (isRule ? ' rule-match' : '');
      if (selectedTagIds.indexOf(tag.id) !== -1) chip.className += ' added';
      chip.textContent = (isRule ? '\u2605 ' : '') + tag.name;
      chip.onclick = function () {
        if (selectedTagIds.indexOf(tag.id) !== -1) {
          removeTag(tag.id);
        } else {
          addTag(tag.id);
        }
      };
      suggestionChipsEl.appendChild(chip);
    }

    ruleMatched.forEach(function (t) { makeChip(t, true); });
    frequent.forEach(function (t) { makeChip(t, false); });
  }

  // Handle tag input
  tagInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      var val = tagInput.value.trim();
      if (val) {
        var tag = matchTagByName(val);
        if (tag) addTag(tag.id);
        else tagInput.value = '';
      } else {
        // Empty input + Enter = submit form (save)
        doSave(false);
      }
    } else if (e.key === 'Backspace' && tagInput.value === '' && selectedTagIds.length > 0) {
      removeTag(selectedTagIds[selectedTagIds.length - 1]);
    } else if (e.key === 'Escape') {
      window.close();
    }
  });

  tagInput.addEventListener('input', function () {
    // Detect datalist selection (browser fills the value)
    var val = tagInput.value.trim();
    if (val) {
      var tag = matchTagByName(val);
      // Only auto-accept if it's an exact match (datalist selection)
      if (tag && val.toLowerCase() === tag.name.toLowerCase()) {
        // Delay to let the browser finish the datalist interaction
        setTimeout(function () {
          if (tagInput.value.trim().toLowerCase() === tag.name.toLowerCase()) {
            addTag(tag.id);
          }
        }, 50);
      }
    }
  });

  // Comma/space as tag separator
  tagInput.addEventListener('keypress', function (e) {
    if (e.key === ',' || e.key === ' ') {
      var val = tagInput.value.trim().replace(/,/g, '');
      if (val) {
        var tag = matchTagByName(val);
        if (tag) {
          e.preventDefault();
          addTag(tag.id);
        }
      }
    }
  });

  // Ctrl+Enter / Cmd+Enter = save audio
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      doSave(true);
    }
  });

  btnSave.onclick = function () { doSave(false); };
  btnAudio.onclick = function () { doSave(true); };

  function doSave(withAudio) {
    if (saving) return;
    saving = true;
    btnSave.disabled = true;
    btnAudio.disabled = true;
    statusEl.innerHTML = '<span class="spinner"></span>Saving\u2026';

    var body = { url: pageUrl, title: titleInput.value.trim() || undefined };
    if (selectedTagIds.length > 0) body.tag_ids = selectedTagIds;
    if (withAudio) body.listen_later = true;

    fetch('/api/articles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    })
      .then(function (r) {
        if (r.ok) {
          statusEl.innerHTML = '';
          statusEl.className = 'status';
          statusEl.textContent = withAudio ? 'Saved! Audio will be generated.' : 'Saved!';
          setTimeout(function () { window.close(); }, 1500);
        } else if (r.status === 401) {
          window.location.href = '/?url=' + encodeURIComponent(pageUrl);
        } else if (r.status === 409) {
          statusEl.textContent = 'Already saved.';
          setTimeout(function () { window.close(); }, 1500);
        } else {
          statusEl.className = 'status error';
          statusEl.textContent = 'Error (' + r.status + ')';
          saving = false;
          btnSave.disabled = false;
          btnAudio.disabled = false;
        }
      })
      .catch(function () {
        statusEl.className = 'status error';
        statusEl.textContent = 'Network error';
        saving = false;
        btnSave.disabled = false;
        btnAudio.disabled = false;
      });
  }
})();
