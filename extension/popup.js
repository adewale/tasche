/**
 * Tasche Browser Extension - Popup Script
 *
 * Handles the popup UI: setup, saving articles, and settings.
 * Uses chrome.storage.local for persisting the instance URL.
 * Uses chrome.scripting.executeScript to capture page content.
 */

(function () {
  'use strict';

  // --- DOM refs ---

  var setupSection = document.getElementById('setup-section');
  var saveSection = document.getElementById('save-section');
  var settingsSection = document.getElementById('settings-section');
  var setupForm = document.getElementById('setup-form');
  var instanceUrlInput = document.getElementById('instance-url');
  var pageTitle = document.getElementById('page-title');
  var pageUrl = document.getElementById('page-url');
  var tagInput = document.getElementById('tag-input');
  var saveBtn = document.getElementById('save-btn');
  var saveBtnText = document.getElementById('save-btn-text');
  var saveBtnSpinner = document.getElementById('save-btn-spinner');
  var statusSuccess = document.getElementById('status-success');
  var statusError = document.getElementById('status-error');
  var statusDuplicate = document.getElementById('status-duplicate');
  var articleLink = document.getElementById('article-link');
  var duplicateLink = document.getElementById('duplicate-link');
  var errorDetail = document.getElementById('error-detail');
  var settingsBtn = document.getElementById('settings-btn');
  var settingsBackBtn = document.getElementById('settings-back-btn');
  var settingsForm = document.getElementById('settings-form');
  var settingsInstanceUrlInput = document.getElementById('settings-instance-url');
  var clearBtn = document.getElementById('clear-btn');

  // --- State ---

  var instanceUrl = '';
  var currentTab = null;

  // --- Helpers ---

  function show(el) {
    el.classList.remove('hidden');
  }

  function hide(el) {
    el.classList.add('hidden');
  }

  function hideAllStatuses() {
    hide(statusSuccess);
    hide(statusError);
    hide(statusDuplicate);
  }

  function showSection(section) {
    hide(setupSection);
    hide(saveSection);
    hide(settingsSection);
    show(section);
  }

  function normalizeUrl(url) {
    // Remove trailing slash
    return url.replace(/\/+$/, '');
  }

  function setSaving(isSaving) {
    saveBtn.disabled = isSaving;
    if (isSaving) {
      hide(saveBtnText);
      show(saveBtnSpinner);
    } else {
      show(saveBtnText);
      hide(saveBtnSpinner);
    }
  }

  // --- Content capture ---

  function capturePageContent(tab) {
    return new Promise(function (resolve) {
      // Try to capture the full HTML of the active tab.
      // This may fail for privileged pages (chrome://, about://, etc.)
      chrome.scripting.executeScript(
        {
          target: { tabId: tab.id },
          func: function () {
            return document.documentElement.outerHTML;
          },
        },
        function (results) {
          if (chrome.runtime.lastError || !results || !results[0]) {
            // Content capture failed -- not critical, the server will
            // fetch the page itself.
            resolve(null);
            return;
          }
          resolve(results[0].result);
        }
      );
    });
  }

  // --- API ---

  function saveArticle(url, title, content) {
    var body = { url: url };
    if (title) {
      body.title = title;
    }
    if (content) {
      body.content = content;
    }

    return fetch(instanceUrl + '/api/articles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(body),
    }).then(function (resp) {
      if (!resp.ok) {
        return resp.json().catch(function () {
          return { detail: 'HTTP ' + resp.status };
        }).then(function (err) {
          var e = new Error(err.detail || 'Request failed');
          e.status = resp.status;
          throw e;
        });
      }
      return resp.json();
    });
  }

  // --- Init ---

  function init() {
    chrome.storage.local.get(['instanceUrl'], function (data) {
      if (data.instanceUrl) {
        instanceUrl = data.instanceUrl;
        showSaveView();
      } else {
        showSection(setupSection);
      }
    });
  }

  function showSaveView() {
    showSection(saveSection);
    hideAllStatuses();

    // Get the active tab info
    chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
      if (tabs && tabs[0]) {
        currentTab = tabs[0];
        pageTitle.textContent = currentTab.title || 'Untitled';
        pageUrl.textContent = currentTab.url || '';
      } else {
        pageTitle.textContent = 'Unable to detect page';
        pageUrl.textContent = '';
      }
    });
  }

  // --- Event handlers ---

  // Setup form: save instance URL
  setupForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var url = normalizeUrl(instanceUrlInput.value.trim());
    if (!url) return;

    instanceUrl = url;
    chrome.storage.local.set({ instanceUrl: url }, function () {
      showSaveView();
    });
  });

  // Settings button
  settingsBtn.addEventListener('click', function () {
    settingsInstanceUrlInput.value = instanceUrl;
    showSection(settingsSection);
  });

  // Settings back button
  settingsBackBtn.addEventListener('click', function () {
    showSaveView();
  });

  // Settings form: update instance URL
  settingsForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var url = normalizeUrl(settingsInstanceUrlInput.value.trim());
    if (!url) return;

    instanceUrl = url;
    chrome.storage.local.set({ instanceUrl: url }, function () {
      showSaveView();
    });
  });

  // Clear / disconnect button
  clearBtn.addEventListener('click', function () {
    instanceUrl = '';
    chrome.storage.local.remove(['instanceUrl'], function () {
      instanceUrlInput.value = '';
      showSection(setupSection);
    });
  });

  // Save button
  saveBtn.addEventListener('click', function () {
    if (!currentTab || !currentTab.url) return;

    hideAllStatuses();
    setSaving(true);

    // Capture page content, then save
    capturePageContent(currentTab)
      .then(function (content) {
        return saveArticle(currentTab.url, currentTab.title, content);
      })
      .then(function (result) {
        setSaving(false);

        if (result.updated) {
          // Duplicate URL -- re-processed
          var dupHref = instanceUrl + '/#/article/' + result.id;
          duplicateLink.href = dupHref;
          show(statusDuplicate);
        } else {
          // New article saved
          var href = instanceUrl + '/#/article/' + result.id;
          articleLink.href = href;
          show(statusSuccess);
        }

        // Disable save button after success to prevent double-saves
        saveBtn.disabled = true;
      })
      .catch(function (err) {
        setSaving(false);

        var message = err.message || 'Unknown error';
        if (err.status === 401) {
          message = 'Not authenticated. Please log in to your Tasche instance first.';
        } else if (err.message === 'Failed to fetch') {
          message = 'Could not connect to ' + instanceUrl + '. Check the URL and try again.';
        }

        errorDetail.textContent = message;
        show(statusError);
      });
  });

  // --- Start ---

  init();
})();
