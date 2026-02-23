/**
 * Tasche Browser Extension - Background Service Worker
 *
 * Provides a context menu item for saving articles to Tasche.
 * The actual save logic runs in the popup; the context menu opens
 * the popup or performs a direct save via the API.
 */

// Create context menu on install
chrome.runtime.onInstalled.addListener(function () {
  chrome.contextMenus.create({
    id: 'save-to-tasche',
    title: 'Save to Tasche',
    contexts: ['page', 'link'],
  });
});

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener(function (info, tab) {
  if (info.menuItemId !== 'save-to-tasche') return;

  // Determine the URL to save: use the link URL if right-clicking a link,
  // otherwise use the page URL.
  var urlToSave = info.linkUrl || info.pageUrl || (tab && tab.url);
  var title = tab ? tab.title : '';

  if (!urlToSave) return;

  chrome.storage.local.get(['instanceUrl'], function (data) {
    if (!data.instanceUrl) {
      // No instance configured -- open the popup for setup.
      // Cannot programmatically open the popup, so show a notification-like
      // badge to prompt the user.
      chrome.action.setBadgeText({ text: '!' });
      chrome.action.setBadgeBackgroundColor({ color: '#d4302b' });
      return;
    }

    var body = { url: urlToSave };
    if (title) {
      body.title = title;
    }

    // If saving the current page (not a link), try to capture content
    if (!info.linkUrl && tab && tab.id) {
      chrome.scripting.executeScript(
        {
          target: { tabId: tab.id },
          func: function () {
            return document.documentElement.outerHTML;
          },
        },
        function (results) {
          var content = null;
          if (!chrome.runtime.lastError && results && results[0]) {
            content = results[0].result;
          }
          if (content) {
            body.content = content;
          }
          performSave(data.instanceUrl, body);
        }
      );
    } else {
      performSave(data.instanceUrl, body);
    }
  });
});

function performSave(instanceUrl, body) {
  fetch(instanceUrl + '/api/articles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
  })
    .then(function (resp) {
      if (resp.ok) {
        // Show a brief success badge
        chrome.action.setBadgeText({ text: '' });
        chrome.action.setBadgeBackgroundColor({ color: '#2d8a4e' });
        chrome.action.setBadgeText({ text: 'OK' });
        setTimeout(function () {
          chrome.action.setBadgeText({ text: '' });
        }, 2000);
      } else {
        chrome.action.setBadgeText({ text: 'ERR' });
        chrome.action.setBadgeBackgroundColor({ color: '#d4302b' });
        setTimeout(function () {
          chrome.action.setBadgeText({ text: '' });
        }, 3000);
      }
    })
    .catch(function () {
      chrome.action.setBadgeText({ text: 'ERR' });
      chrome.action.setBadgeBackgroundColor({ color: '#d4302b' });
      setTimeout(function () {
        chrome.action.setBadgeText({ text: '' });
      }, 3000);
    });
}
