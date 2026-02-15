/**
 * Tasche — Vanilla JS SPA
 *
 * A self-hosted read-it-later progressive web app.
 * No frameworks, no build step.
 */

(function () {
  'use strict';

  // =========================================================================
  // API Client
  // =========================================================================

  const api = {
    async request(method, path, body) {
      const opts = {
        method,
        headers: {},
        credentials: 'include',
      };
      if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
      }
      const resp = await fetch(path, opts);
      if (resp.status === 401) {
        state.user = null;
        router.navigate('/login');
        throw new Error('Unauthorized');
      }
      if (resp.status === 204) return null;
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || 'Request failed');
      }
      if (resp.headers.get('content-type')?.includes('application/json')) {
        return resp.json();
      }
      return resp;
    },

    // Auth
    getSession() { return this.request('GET', '/api/auth/session'); },
    logout() { return this.request('POST', '/api/auth/logout'); },

    // Articles
    listArticles(params) {
      const qs = new URLSearchParams();
      if (params.reading_status) qs.set('reading_status', params.reading_status);
      if (params.is_favorite !== undefined) qs.set('is_favorite', params.is_favorite);
      if (params.tag) qs.set('tag', params.tag);
      if (params.limit) qs.set('limit', params.limit);
      if (params.offset) qs.set('offset', params.offset);
      return this.request('GET', '/api/articles?' + qs.toString());
    },
    getArticle(id) { return this.request('GET', '/api/articles/' + id); },
    createArticle(url, title) {
      const body = { url };
      if (title) body.title = title;
      return this.request('POST', '/api/articles', body);
    },
    updateArticle(id, data) { return this.request('PATCH', '/api/articles/' + id, data); },
    deleteArticle(id) { return this.request('DELETE', '/api/articles/' + id); },

    // Search
    search(q, limit, offset) {
      const qs = new URLSearchParams({ q });
      if (limit) qs.set('limit', limit);
      if (offset) qs.set('offset', offset);
      return this.request('GET', '/api/search?' + qs.toString());
    },

    // Tags
    listTags() { return this.request('GET', '/api/tags'); },
    createTag(name) { return this.request('POST', '/api/tags', { name }); },
    deleteTag(id) { return this.request('DELETE', '/api/tags/' + id); },
    getArticleTags(articleId) { return this.request('GET', '/api/articles/' + articleId + '/tags'); },
    addTagToArticle(articleId, tagId) { return this.request('POST', '/api/articles/' + articleId + '/tags', { tag_id: tagId }); },
    removeTagFromArticle(articleId, tagId) { return this.request('DELETE', '/api/articles/' + articleId + '/tags/' + tagId); },

    // TTS / Audio
    listenLater(articleId) { return this.request('POST', '/api/articles/' + articleId + '/listen-later'); },
  };

  // =========================================================================
  // Application State
  // =========================================================================

  const state = {
    user: null,
    articles: [],
    currentArticle: null,
    tags: [],
    searchResults: [],
    searchQuery: '',
    filter: 'all', // all, unread, reading, archived, favorites
    offset: 0,
    limit: 20,
    hasMore: true,
    loading: false,
    online: navigator.onLine,
  };

  // =========================================================================
  // Toast Notifications
  // =========================================================================

  const toast = {
    container: null,

    init() {
      this.container = document.createElement('div');
      this.container.className = 'toast-container';
      document.body.appendChild(this.container);
    },

    show(message, type) {
      type = type || 'info';
      const el = document.createElement('div');
      el.className = 'toast ' + type;
      el.textContent = message;
      this.container.appendChild(el);
      setTimeout(() => {
        el.style.opacity = '0';
        el.style.transition = 'opacity 0.3s';
        setTimeout(() => el.remove(), 300);
      }, 3000);
    },

    success(msg) { this.show(msg, 'success'); },
    error(msg) { this.show(msg, 'error'); },
    info(msg) { this.show(msg, 'info'); },
  };

  // =========================================================================
  // Audio Player
  // =========================================================================

  const audioPlayer = {
    audio: null,
    articleId: null,
    articleTitle: '',
    speeds: [0.75, 1, 1.25, 1.5, 1.75, 2],
    speedIndex: 1,
    barEl: null,

    init() {
      this.audio = new Audio();
      this.audio.addEventListener('timeupdate', () => this.updateProgress());
      this.audio.addEventListener('ended', () => this.onEnded());
      this.audio.addEventListener('error', () => {
        toast.error('Audio playback error');
      });
    },

    play(articleId, title) {
      this.articleId = articleId;
      this.articleTitle = title || 'Untitled';
      this.audio.src = '/api/articles/' + articleId + '/audio';
      this.audio.playbackRate = this.speeds[this.speedIndex];
      this.audio.play().catch((e) => toast.error('Could not play audio: ' + e.message));
      this.renderBar();
      document.body.classList.add('has-audio-player');
    },

    toggle() {
      if (!this.audio.src) return;
      if (this.audio.paused) {
        this.audio.play().catch(() => {});
      } else {
        this.audio.pause();
      }
      this.renderBar();
    },

    skip(seconds) {
      if (!this.audio.src) return;
      this.audio.currentTime = Math.max(0, Math.min(this.audio.duration || 0, this.audio.currentTime + seconds));
    },

    cycleSpeed() {
      this.speedIndex = (this.speedIndex + 1) % this.speeds.length;
      this.audio.playbackRate = this.speeds[this.speedIndex];
      this.renderBar();
    },

    stop() {
      this.audio.pause();
      this.audio.src = '';
      this.articleId = null;
      document.body.classList.remove('has-audio-player');
      if (this.barEl) {
        this.barEl.classList.remove('visible');
      }
    },

    onEnded() {
      this.renderBar();
    },

    updateProgress() {
      if (!this.barEl) return;
      const progressBar = this.barEl.querySelector('.audio-progress-bar');
      const timeEl = this.barEl.querySelector('.audio-player-time');
      if (progressBar && this.audio.duration) {
        const pct = (this.audio.currentTime / this.audio.duration) * 100;
        progressBar.style.width = pct + '%';
      }
      if (timeEl) {
        timeEl.textContent = formatTime(this.audio.currentTime) + ' / ' + formatTime(this.audio.duration || 0);
      }
    },

    renderBar() {
      if (!this.barEl) {
        this.barEl = document.createElement('div');
        this.barEl.className = 'audio-player-bar';
        document.body.appendChild(this.barEl);
      }

      const isPlaying = !this.audio.paused;

      this.barEl.innerHTML =
        '<div class="audio-player-inner">' +
          '<div class="audio-player-info">' +
            '<div class="audio-player-title">' + escapeHtml(this.articleTitle) + '</div>' +
            '<div class="audio-player-time">0:00 / 0:00</div>' +
          '</div>' +
          '<div class="audio-player-controls">' +
            '<button class="audio-skip-back" title="Back 15s">\u23EA</button>' +
            '<button class="play-btn" title="' + (isPlaying ? 'Pause' : 'Play') + '">' +
              (isPlaying ? '\u23F8' : '\u25B6') +
            '</button>' +
            '<button class="audio-skip-fwd" title="Forward 15s">\u23E9</button>' +
            '<button class="audio-speed-btn" title="Playback speed">' + this.speeds[this.speedIndex] + 'x</button>' +
            '<button class="audio-close-btn" title="Close">\u2715</button>' +
          '</div>' +
        '</div>' +
        '<div class="audio-progress"><div class="audio-progress-bar" style="width:0%"></div></div>';

      this.barEl.classList.add('visible');

      // Event listeners
      this.barEl.querySelector('.play-btn').addEventListener('click', () => this.toggle());
      this.barEl.querySelector('.audio-skip-back').addEventListener('click', () => this.skip(-15));
      this.barEl.querySelector('.audio-skip-fwd').addEventListener('click', () => this.skip(15));
      this.barEl.querySelector('.audio-speed-btn').addEventListener('click', () => this.cycleSpeed());
      this.barEl.querySelector('.audio-close-btn').addEventListener('click', () => this.stop());

      // Seek on click
      const progressEl = this.barEl.querySelector('.audio-progress');
      progressEl.addEventListener('click', (e) => {
        if (!this.audio.duration) return;
        const rect = progressEl.getBoundingClientRect();
        const pct = (e.clientX - rect.left) / rect.width;
        this.audio.currentTime = pct * this.audio.duration;
      });

      this.updateProgress();
    },
  };

  // =========================================================================
  // Router
  // =========================================================================

  const router = {
    routes: {},

    register(pattern, handler) {
      this.routes[pattern] = handler;
    },

    navigate(hash) {
      window.location.hash = hash;
    },

    currentHash() {
      return window.location.hash.slice(1) || '/';
    },

    resolve() {
      const hash = this.currentHash();

      // Try exact match first
      if (this.routes[hash]) {
        this.routes[hash]();
        return;
      }

      // Try pattern matching for /article/:id
      for (const pattern of Object.keys(this.routes)) {
        const regex = pattern.replace(/:(\w+)/g, '([^/]+)');
        const match = hash.match(new RegExp('^' + regex + '$'));
        if (match) {
          this.routes[pattern](match[1]);
          return;
        }
      }

      // Default to article list
      this.routes['/']();
    },
  };

  // =========================================================================
  // Utility Functions
  // =========================================================================

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function formatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    const mins = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    if (hours < 24) return hours + 'h ago';
    if (days < 7) return days + 'd ago';

    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
  }

  function formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return '0:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function $(selector, parent) {
    return (parent || document).querySelector(selector);
  }

  function setContent(html) {
    var el = $('#app');
    if (el) el.innerHTML = html;
  }

  // Queue an offline mutation for background sync
  function queueOfflineMutation(url, method, body) {
    if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({
        type: 'QUEUE_REQUEST',
        request: {
          url: url,
          method: method,
          headers: { 'Content-Type': 'application/json' },
          body: body ? JSON.stringify(body) : undefined,
        },
      });
      if ('sync' in window.SyncManager) {
        navigator.serviceWorker.ready.then(function (reg) {
          return reg.sync.register('tasche-sync');
        }).catch(function () {});
      }
    }
  }

  // =========================================================================
  // View: Login
  // =========================================================================

  function getBookmarkletCode() {
    var origin = window.location.origin;
    return "javascript:void(window.open('" + origin + "/?url='+encodeURIComponent(location.href)+'&title='+encodeURIComponent(document.title)))";
  }

  function renderLogin() {
    setContent(
      '<div class="login-page">' +
        '<h1>Tasche</h1>' +
        '<p>Save articles. Read later. Listen anywhere.</p>' +
        '<a href="/api/auth/login" class="btn btn-primary login-btn">Sign in with GitHub</a>' +
      '</div>'
    );
  }

  // =========================================================================
  // View: Header
  // =========================================================================

  function renderHeader() {
    const user = state.user;
    const avatarHtml = user && user.avatar_url
      ? '<img class="user-avatar" src="' + escapeHtml(user.avatar_url) + '" alt="Avatar">'
      : '';

    return (
      '<header class="header">' +
        '<div class="header-inner">' +
          '<a href="#/" class="header-logo">Tasche</a>' +
          '<div class="header-actions">' +
            '<a href="#/search" class="btn btn-icon" title="Search">\uD83D\uDD0D</a>' +
            '<a href="#/tags" class="btn btn-icon" title="Tags">\uD83C\uDFF7\uFE0F</a>' +
            avatarHtml +
            '<button class="btn btn-sm btn-secondary" id="logout-btn">Logout</button>' +
          '</div>' +
        '</div>' +
      '</header>' +
      '<div class="offline-bar" id="offline-bar">You are offline. Some features may be unavailable.</div>'
    );
  }

  function bindHeaderEvents() {
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', async function () {
        try {
          await api.logout();
        } catch (e) {
          // ignore
        }
        state.user = null;
        router.navigate('/login');
      });
    }

    // Update offline bar
    var offlineBar = document.getElementById('offline-bar');
    if (offlineBar) {
      offlineBar.classList.toggle('visible', !state.online);
    }
  }

  // =========================================================================
  // View: Article List (#/)
  // =========================================================================

  function renderArticleList() {
    state.articles = [];
    state.offset = 0;
    state.hasMore = true;

    setContent(
      renderHeader() +
      '<main class="main-content">' +
        '<div class="save-form">' +
          '<div class="input-group">' +
            '<input class="input" type="url" id="save-url" placeholder="Paste a URL to save..." autocomplete="off">' +
            '<button class="btn btn-primary" id="save-btn">Save</button>' +
          '</div>' +
        '</div>' +
        '<div class="filter-tabs" id="filter-tabs">' +
          renderFilterTabs() +
        '</div>' +
        '<div class="article-list" id="article-list"></div>' +
        '<div class="load-more" id="load-more" style="display:none">' +
          '<button class="btn btn-secondary" id="load-more-btn">Load more</button>' +
        '</div>' +
        '<div class="loading" id="articles-loading"><div class="spinner"></div></div>' +
      '</main>'
    );

    bindHeaderEvents();
    bindArticleListEvents();
    loadArticles();
  }

  function renderFilterTabs() {
    var filters = [
      { key: 'all', label: 'All' },
      { key: 'unread', label: 'Unread' },
      { key: 'reading', label: 'Reading' },
      { key: 'archived', label: 'Archived' },
      { key: 'favorites', label: 'Favorites' },
    ];
    return filters.map(function (f) {
      return '<button class="filter-tab' + (state.filter === f.key ? ' active' : '') + '" data-filter="' + f.key + '">' + f.label + '</button>';
    }).join('');
  }

  function bindArticleListEvents() {
    // Save button
    var saveBtn = document.getElementById('save-btn');
    var saveInput = document.getElementById('save-url');
    if (saveBtn) {
      saveBtn.addEventListener('click', function () { saveArticle(); });
    }
    if (saveInput) {
      saveInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') saveArticle();
      });
    }

    // Filter tabs
    var tabsEl = document.getElementById('filter-tabs');
    if (tabsEl) {
      tabsEl.addEventListener('click', function (e) {
        var tab = e.target.closest('.filter-tab');
        if (!tab) return;
        state.filter = tab.dataset.filter;
        state.articles = [];
        state.offset = 0;
        state.hasMore = true;
        tabsEl.innerHTML = renderFilterTabs();
        loadArticles();
      });
    }

    // Load more button
    var loadMoreBtn = document.getElementById('load-more-btn');
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener('click', function () {
        loadArticles();
      });
    }
  }

  async function saveArticle() {
    var input = document.getElementById('save-url');
    var url = input ? input.value.trim() : '';
    if (!url) {
      toast.error('Please enter a URL');
      return;
    }

    try {
      await api.createArticle(url);
      toast.success('Article saved!');
      if (input) input.value = '';
      // Reload list
      state.articles = [];
      state.offset = 0;
      state.hasMore = true;
      loadArticles();
    } catch (e) {
      if (!state.online) {
        queueOfflineMutation('/api/articles', 'POST', { url: url });
        toast.info('Saved offline. Will sync when back online.');
        if (input) input.value = '';
      } else {
        toast.error(e.message);
      }
    }
  }

  async function loadArticles() {
    if (state.loading || !state.hasMore) return;
    state.loading = true;

    var loadingEl = document.getElementById('articles-loading');
    var loadMoreEl = document.getElementById('load-more');
    if (loadingEl) loadingEl.style.display = 'flex';
    if (loadMoreEl) loadMoreEl.style.display = 'none';

    try {
      var params = { limit: state.limit, offset: state.offset };
      if (state.filter === 'unread') params.reading_status = 'unread';
      else if (state.filter === 'reading') params.reading_status = 'reading';
      else if (state.filter === 'archived') params.reading_status = 'archived';
      else if (state.filter === 'favorites') params.is_favorite = 1;

      var articles = await api.listArticles(params);
      state.articles = state.articles.concat(articles);
      state.offset += articles.length;
      state.hasMore = articles.length >= state.limit;

      renderArticleCards();

      // Pre-cache unread articles for offline reading
      if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
        var unreadIds = articles.filter(function (a) { return a.reading_status === 'unread'; }).map(function (a) { return a.id; });
        if (unreadIds.length > 0) {
          navigator.serviceWorker.controller.postMessage({ type: 'CACHE_ARTICLES', articleIds: unreadIds });
        }
      }
    } catch (e) {
      toast.error('Failed to load articles: ' + e.message);
    } finally {
      state.loading = false;
      if (loadingEl) loadingEl.style.display = 'none';
      if (loadMoreEl) loadMoreEl.style.display = state.hasMore ? 'flex' : 'none';
    }
  }

  function renderArticleCards() {
    var listEl = document.getElementById('article-list');
    if (!listEl) return;

    if (state.articles.length === 0) {
      listEl.innerHTML =
        '<div class="empty-state">' +
          '<div class="empty-state-icon">\uD83D\uDCDA</div>' +
          '<div class="empty-state-title">No articles yet</div>' +
          '<div class="empty-state-text">Save a URL above to get started.</div>' +
        '</div>';
      return;
    }

    listEl.innerHTML = state.articles.map(function (a) {
      var readingTime = a.reading_time_minutes ? a.reading_time_minutes + ' min read' : '';
      var statusClass = a.reading_status || 'unread';
      var isFav = a.is_favorite ? ' favorited' : '';
      var progress = a.reading_progress ? parseFloat(a.reading_progress) : 0;
      var progressHtml = progress > 0
        ? '<div class="reading-progress-bar"><div class="reading-progress-bar-fill" style="width:' + Math.round(progress * 100) + '%"></div></div>'
        : '';

      return (
        '<div class="article-card" data-id="' + escapeHtml(a.id) + '">' +
          '<div class="article-card-title">' + escapeHtml(a.title || a.original_url) + '</div>' +
          '<div class="article-card-meta">' +
            '<span class="article-card-domain">' + escapeHtml(a.domain || '') + '</span>' +
            (readingTime ? '<span>' + readingTime + '</span>' : '') +
            '<span>' + formatDate(a.created_at) + '</span>' +
            '<span class="reading-status-badge ' + statusClass + '">' + statusClass + '</span>' +
          '</div>' +
          (a.excerpt ? '<div class="article-card-excerpt">' + escapeHtml(a.excerpt) + '</div>' : '') +
          progressHtml +
          '<div class="article-card-footer">' +
            '<div class="article-card-tags"></div>' +
            '<div class="article-card-actions">' +
              '<button class="fav-btn' + isFav + '" data-id="' + escapeHtml(a.id) + '" data-fav="' + (a.is_favorite ? '1' : '0') + '" title="Toggle favorite">' +
                (a.is_favorite ? '\u2605' : '\u2606') +
              '</button>' +
              '<button class="delete-btn" data-id="' + escapeHtml(a.id) + '" title="Delete">\uD83D\uDDD1</button>' +
            '</div>' +
          '</div>' +
        '</div>'
      );
    }).join('');

    // Bind card click events
    listEl.querySelectorAll('.article-card').forEach(function (card) {
      card.addEventListener('click', function (e) {
        // Don't navigate if clicking action buttons
        if (e.target.closest('.article-card-actions')) return;
        var id = card.dataset.id;
        router.navigate('/article/' + id);
      });
    });

    // Favorite toggle
    listEl.querySelectorAll('.fav-btn').forEach(function (btn) {
      btn.addEventListener('click', async function (e) {
        e.stopPropagation();
        var id = btn.dataset.id;
        var currentFav = btn.dataset.fav === '1';
        var newFav = !currentFav;

        try {
          await api.updateArticle(id, { is_favorite: newFav });
          btn.dataset.fav = newFav ? '1' : '0';
          btn.textContent = newFav ? '\u2605' : '\u2606';
          btn.classList.toggle('favorited', newFav);
          // Update state
          var article = state.articles.find(function (a) { return a.id === id; });
          if (article) article.is_favorite = newFav ? 1 : 0;
        } catch (e2) {
          if (!state.online) {
            queueOfflineMutation('/api/articles/' + id, 'PATCH', { is_favorite: newFav });
            btn.dataset.fav = newFav ? '1' : '0';
            btn.textContent = newFav ? '\u2605' : '\u2606';
            btn.classList.toggle('favorited', newFav);
            toast.info('Queued for sync');
          } else {
            toast.error(e2.message);
          }
        }
      });
    });

    // Delete buttons
    listEl.querySelectorAll('.delete-btn').forEach(function (btn) {
      btn.addEventListener('click', async function (e) {
        e.stopPropagation();
        if (!confirm('Delete this article?')) return;
        var id = btn.dataset.id;
        try {
          await api.deleteArticle(id);
          state.articles = state.articles.filter(function (a) { return a.id !== id; });
          renderArticleCards();
          toast.success('Article deleted');
        } catch (e2) {
          toast.error(e2.message);
        }
      });
    });
  }

  // =========================================================================
  // View: Reader (#/article/:id)
  // =========================================================================

  async function renderReader(id) {
    setContent(
      renderHeader() +
      '<main class="main-content">' +
        '<div class="loading"><div class="spinner"></div></div>' +
      '</main>'
    );
    bindHeaderEvents();

    try {
      var article = await api.getArticle(id);
      state.currentArticle = article;

      // Also load tags for this article
      var articleTags = [];
      try {
        articleTags = await api.getArticleTags(id);
      } catch (e) {
        // tags might fail, that's ok
      }

      var readingTime = article.reading_time_minutes ? article.reading_time_minutes + ' min read' : '';
      var statusClass = article.reading_status || 'unread';
      var isFav = article.is_favorite;
      var hasAudio = article.audio_status === 'ready';
      var canRequestAudio = !article.listen_later && article.audio_status !== 'pending' && article.audio_status !== 'generating';
      var audioPending = article.audio_status === 'pending' || article.audio_status === 'generating';

      // Build tags HTML
      var tagsHtml = articleTags.map(function (t) {
        return '<span class="tag-chip">' + escapeHtml(t.name) +
          '<span class="tag-chip-remove" data-tag-id="' + escapeHtml(t.id) + '" data-article-id="' + escapeHtml(id) + '">\u00D7</span></span>';
      }).join('');

      // Determine content to show
      var contentHtml = '';
      if (article.markdown_content) {
        contentHtml = renderMarkdown(article.markdown_content);
      } else if (article.excerpt) {
        contentHtml = '<p>' + escapeHtml(article.excerpt) + '</p>';
      } else if (article.status === 'pending') {
        contentHtml = '<p class="text-muted">Article is being processed. Refresh in a moment.</p>';
      } else {
        contentHtml = '<p class="text-muted">No content available. <a href="' + escapeHtml(article.original_url) + '" target="_blank" rel="noopener">View original</a></p>';
      }

      $('.main-content').innerHTML =
        '<div class="reader-header">' +
          '<a href="#/" class="reader-back">\u2190 Back to articles</a>' +
          '<h1 class="reader-title">' + escapeHtml(article.title || 'Untitled') + '</h1>' +
          '<div class="reader-meta">' +
            (article.author ? '<span class="reader-meta-item">' + escapeHtml(article.author) + '</span>' : '') +
            (article.domain ? '<span class="reader-meta-item"><a href="' + escapeHtml(article.original_url) + '" target="_blank" rel="noopener">' + escapeHtml(article.domain) + '</a></span>' : '') +
            (readingTime ? '<span class="reader-meta-item">' + readingTime + '</span>' : '') +
            (article.word_count ? '<span class="reader-meta-item">' + article.word_count.toLocaleString() + ' words</span>' : '') +
          '</div>' +
          '<div class="reader-actions">' +
            '<button class="btn btn-sm ' + (isFav ? 'btn-primary' : 'btn-secondary') + '" id="reader-fav-btn">' +
              (isFav ? '\u2605 Favorited' : '\u2606 Favorite') +
            '</button>' +
            '<select class="input" id="reader-status-select" style="width:auto;padding:4px 10px;font-size:0.8125rem;">' +
              '<option value="unread"' + (statusClass === 'unread' ? ' selected' : '') + '>Unread</option>' +
              '<option value="reading"' + (statusClass === 'reading' ? ' selected' : '') + '>Reading</option>' +
              '<option value="archived"' + (statusClass === 'archived' ? ' selected' : '') + '>Archived</option>' +
            '</select>' +
            (hasAudio ? '<button class="btn btn-sm btn-secondary" id="play-audio-btn">\u25B6 Listen</button>' : '') +
            (canRequestAudio ? '<button class="btn btn-sm btn-secondary" id="request-audio-btn">\uD83C\uDFA7 Listen Later</button>' : '') +
            (audioPending ? '<span class="btn btn-sm btn-secondary" disabled>\u23F3 Generating audio...</span>' : '') +
            '<a href="' + escapeHtml(article.original_url) + '" target="_blank" rel="noopener" class="btn btn-sm btn-secondary">\u2197 Original</a>' +
          '</div>' +
          '<div class="flex flex-wrap gap-2 mt-4" id="reader-tags-container">' + tagsHtml +
            '<button class="tag-chip" id="add-tag-btn" title="Add tag">+ Tag</button>' +
          '</div>' +
          '<div id="tag-picker" class="tag-picker" style="display:none">' +
            '<select class="input" id="tag-picker-select" style="width:auto;padding:4px 10px;font-size:0.8125rem;">' +
              '<option value="">Select a tag...</option>' +
            '</select>' +
            '<button class="btn btn-sm btn-primary" id="tag-picker-add">Add</button>' +
            '<button class="btn btn-sm btn-secondary" id="tag-picker-cancel">Cancel</button>' +
          '</div>' +
        '</div>' +
        '<article class="reader-content">' + contentHtml + '</article>';

      // Mark as reading if currently unread
      if (article.reading_status === 'unread') {
        api.updateArticle(id, { reading_status: 'reading' }).catch(function () {});
      }

      bindReaderEvents(id);
      setupScrollTracking(id);

      // Restore scroll position from percentage
      if (article.scroll_position && parseFloat(article.scroll_position) > 0) {
        setTimeout(function () {
          var pct = parseFloat(article.scroll_position);
          // scroll_position is stored as a percentage (0-1)
          var docHeight = document.documentElement.scrollHeight - document.documentElement.clientHeight;
          var targetScroll = pct * docHeight;
          window.scrollTo(0, targetScroll);
        }, 100);
      }
    } catch (e) {
      $('.main-content').innerHTML =
        '<div class="empty-state">' +
          '<div class="empty-state-title">Could not load article</div>' +
          '<div class="empty-state-text">' + escapeHtml(e.message) + '</div>' +
          '<a href="#/" class="btn btn-secondary mt-4">Back to articles</a>' +
        '</div>';
    }
  }

  function bindReaderEvents(articleId) {
    // Favorite toggle
    var favBtn = document.getElementById('reader-fav-btn');
    if (favBtn) {
      favBtn.addEventListener('click', async function () {
        var article = state.currentArticle;
        if (!article) return;
        var newFav = !article.is_favorite;
        try {
          await api.updateArticle(articleId, { is_favorite: newFav });
          article.is_favorite = newFav ? 1 : 0;
          favBtn.textContent = newFav ? '\u2605 Favorited' : '\u2606 Favorite';
          favBtn.className = 'btn btn-sm ' + (newFav ? 'btn-primary' : 'btn-secondary');
        } catch (e) {
          toast.error(e.message);
        }
      });
    }

    // Reading status
    var statusSelect = document.getElementById('reader-status-select');
    if (statusSelect) {
      statusSelect.addEventListener('change', async function () {
        try {
          await api.updateArticle(articleId, { reading_status: statusSelect.value });
          if (state.currentArticle) state.currentArticle.reading_status = statusSelect.value;
          toast.success('Status updated');
        } catch (e) {
          toast.error(e.message);
        }
      });
    }

    // Play audio
    var playBtn = document.getElementById('play-audio-btn');
    if (playBtn) {
      playBtn.addEventListener('click', function () {
        audioPlayer.play(articleId, state.currentArticle ? state.currentArticle.title : '');
      });
    }

    // Request audio
    var requestAudioBtn = document.getElementById('request-audio-btn');
    if (requestAudioBtn) {
      requestAudioBtn.addEventListener('click', async function () {
        try {
          await api.listenLater(articleId);
          toast.success('Audio generation queued');
          requestAudioBtn.disabled = true;
          requestAudioBtn.textContent = '\u23F3 Generating...';
        } catch (e) {
          toast.error(e.message);
        }
      });
    }

    // Remove tag
    document.querySelectorAll('.tag-chip-remove').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        var tagId = btn.dataset.tagId;
        var artId = btn.dataset.articleId;
        try {
          await api.removeTagFromArticle(artId, tagId);
          btn.parentElement.remove();
          toast.success('Tag removed');
        } catch (e) {
          toast.error(e.message);
        }
      });
    });

    // Add tag picker
    var addTagBtn = document.getElementById('add-tag-btn');
    var tagPicker = document.getElementById('tag-picker');
    var tagSelect = document.getElementById('tag-picker-select');
    var tagPickerAdd = document.getElementById('tag-picker-add');
    var tagPickerCancel = document.getElementById('tag-picker-cancel');

    if (addTagBtn && tagPicker) {
      addTagBtn.addEventListener('click', async function () {
        // Load all tags if not yet loaded
        if (state.tags.length === 0) {
          try { state.tags = await api.listTags(); } catch (e) { /* ignore */ }
        }
        // Populate select with available tags
        tagSelect.innerHTML = '<option value="">Select a tag...</option>';
        state.tags.forEach(function (t) {
          var opt = document.createElement('option');
          opt.value = t.id;
          opt.textContent = t.name;
          tagSelect.appendChild(opt);
        });
        tagPicker.style.display = 'flex';
        addTagBtn.style.display = 'none';
      });

      tagPickerCancel.addEventListener('click', function () {
        tagPicker.style.display = 'none';
        addTagBtn.style.display = '';
      });

      tagPickerAdd.addEventListener('click', async function () {
        var tagId = tagSelect.value;
        if (!tagId) { toast.error('Select a tag'); return; }
        try {
          await api.addTagToArticle(articleId, tagId);
          var tagName = tagSelect.options[tagSelect.selectedIndex].text;
          var container = document.getElementById('reader-tags-container');
          if (container) {
            var chip = document.createElement('span');
            chip.className = 'tag-chip';
            chip.innerHTML = escapeHtml(tagName) +
              '<span class="tag-chip-remove" data-tag-id="' + escapeHtml(tagId) + '" data-article-id="' + escapeHtml(articleId) + '">\u00D7</span>';
            container.insertBefore(chip, addTagBtn);
            chip.querySelector('.tag-chip-remove').addEventListener('click', async function () {
              try {
                await api.removeTagFromArticle(articleId, tagId);
                chip.remove();
                toast.success('Tag removed');
              } catch (e2) { toast.error(e2.message); }
            });
          }
          toast.success('Tag added');
          tagPicker.style.display = 'none';
          addTagBtn.style.display = '';
        } catch (e) {
          toast.error(e.message);
        }
      });
    }
  }

  function setupScrollTracking(articleId) {
    var debounceTimer = null;
    var readerContent = $('.reader-content');
    if (!readerContent) return;

    function onScroll() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        var scrollTop = window.scrollY || document.documentElement.scrollTop;
        var docHeight = document.documentElement.scrollHeight - document.documentElement.clientHeight;
        if (docHeight <= 0) return;
        var progress = Math.min(1, Math.max(0, scrollTop / docHeight));
        // Save scroll position as percentage for cross-device compatibility
        api.updateArticle(articleId, {
          scroll_position: Math.round(progress * 10000) / 10000,
          reading_progress: Math.round(progress * 100) / 100,
        }).catch(function () {});
      }, 1000);
    }

    window.addEventListener('scroll', onScroll);

    // Clean up on navigation
    var origResolve = router.resolve.bind(router);
    router.resolve = function () {
      window.removeEventListener('scroll', onScroll);
      router.resolve = origResolve;
      origResolve();
    };
  }

  // Simple markdown-to-HTML renderer for basic display
  function renderMarkdown(md) {
    if (!md) return '';
    var html = escapeHtml(md);

    // Headers
    html = html.replace(/^######\s+(.+)$/gm, '<h6>$1</h6>');
    html = html.replace(/^#####\s+(.+)$/gm, '<h5>$1</h5>');
    html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');

    // Bold and italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Code blocks
    html = html.replace(/```[\w]*\n([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Blockquotes
    html = html.replace(/^&gt;\s+(.+)$/gm, '<blockquote>$1</blockquote>');

    // Horizontal rules
    html = html.replace(/^---$/gm, '<hr>');

    // Links (sanitize javascript: URLs, decode HTML entities in href)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, text, url) {
      if (/^\s*javascript\s*:/i.test(url.replace(/&amp;/g, '&').replace(/&#/g, '#'))) return text;
      var decodedUrl = url.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
      return '<a href="' + decodedUrl + '" target="_blank" rel="noopener">' + text + '</a>';
    });

    // Images (sanitize javascript: URLs, decode HTML entities in src)
    html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (_, alt, url) {
      if (/^\s*javascript\s*:/i.test(url.replace(/&amp;/g, '&').replace(/&#/g, '#'))) return alt;
      var decodedUrl = url.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
      return '<img src="' + decodedUrl + '" alt="' + alt + '" loading="lazy">';
    });

    // Unordered lists
    html = html.replace(/^[\-\*]\s+(.+)$/gm, '<li>$1</li>');
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');

    // Ordered lists
    html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');

    // Paragraphs: wrap remaining loose text lines
    html = html.replace(/^(?!<[a-z])((?:[^\n])+)$/gm, '<p>$1</p>');

    // Clean up empty paragraphs
    html = html.replace(/<p>\s*<\/p>/g, '');

    return html;
  }

  // =========================================================================
  // View: Search (#/search)
  // =========================================================================

  function renderSearch() {
    state.searchResults = [];

    setContent(
      renderHeader() +
      '<main class="main-content">' +
        '<h2 class="section-title">Search</h2>' +
        '<div class="search-container">' +
          '<div class="input-group">' +
            '<input class="input" type="search" id="search-input" placeholder="Search articles..." value="' + escapeHtml(state.searchQuery) + '" autofocus>' +
            '<button class="btn btn-primary" id="search-btn">Search</button>' +
          '</div>' +
        '</div>' +
        '<div id="search-results-info" class="search-results-info"></div>' +
        '<div class="article-list" id="search-results"></div>' +
        '<div class="loading" id="search-loading" style="display:none"><div class="spinner"></div></div>' +
      '</main>'
    );

    bindHeaderEvents();

    var searchInput = document.getElementById('search-input');
    var searchBtn = document.getElementById('search-btn');

    function doSearch() {
      var q = searchInput ? searchInput.value.trim() : '';
      if (!q) {
        toast.error('Enter a search query');
        return;
      }
      state.searchQuery = q;
      performSearch(q);
    }

    if (searchBtn) searchBtn.addEventListener('click', doSearch);
    if (searchInput) {
      searchInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') doSearch();
      });
      // Auto-search if there's a query
      if (state.searchQuery) {
        performSearch(state.searchQuery);
      }
    }
  }

  async function performSearch(q) {
    var loadingEl = document.getElementById('search-loading');
    var resultsEl = document.getElementById('search-results');
    var infoEl = document.getElementById('search-results-info');
    if (loadingEl) loadingEl.style.display = 'flex';
    if (resultsEl) resultsEl.innerHTML = '';

    try {
      var results = await api.search(q);
      state.searchResults = results;

      if (infoEl) {
        infoEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '') + ' for "' + q + '"';
      }

      if (resultsEl) {
        if (results.length === 0) {
          resultsEl.innerHTML =
            '<div class="empty-state">' +
              '<div class="empty-state-title">No results found</div>' +
              '<div class="empty-state-text">Try a different search query.</div>' +
            '</div>';
        } else {
          resultsEl.innerHTML = results.map(function (a) {
            return (
              '<div class="article-card" data-id="' + escapeHtml(a.id) + '">' +
                '<div class="article-card-title">' + escapeHtml(a.title || a.original_url) + '</div>' +
                '<div class="article-card-meta">' +
                  '<span class="article-card-domain">' + escapeHtml(a.domain || '') + '</span>' +
                  '<span>' + formatDate(a.created_at) + '</span>' +
                '</div>' +
                (a.excerpt ? '<div class="article-card-excerpt">' + escapeHtml(a.excerpt) + '</div>' : '') +
              '</div>'
            );
          }).join('');

          resultsEl.querySelectorAll('.article-card').forEach(function (card) {
            card.addEventListener('click', function () {
              router.navigate('/article/' + card.dataset.id);
            });
          });
        }
      }
    } catch (e) {
      toast.error('Search failed: ' + e.message);
    } finally {
      if (loadingEl) loadingEl.style.display = 'none';
    }
  }

  // =========================================================================
  // View: Tags (#/tags)
  // =========================================================================

  async function renderTags() {
    setContent(
      renderHeader() +
      '<main class="main-content">' +
        '<h2 class="section-title">Tags</h2>' +
        '<div class="input-group mb-4">' +
          '<input class="input" type="text" id="tag-name-input" placeholder="New tag name...">' +
          '<button class="btn btn-primary" id="create-tag-btn">Create Tag</button>' +
        '</div>' +
        '<div class="loading" id="tags-loading"><div class="spinner"></div></div>' +
        '<div class="tags-list" id="tags-list"></div>' +
        '<div class="mt-8">' +
          '<h2 class="section-title">Bookmarklet</h2>' +
          '<p class="text-muted mb-4" style="font-size:0.875rem">Drag this link to your bookmarks bar to save articles from any page:</p>' +
          '<a href="' + escapeHtml(getBookmarkletCode()) + '" class="btn btn-secondary" onclick="return false;" id="bookmarklet-link">\uD83D\uDCCC Save to Tasche</a>' +
        '</div>' +
      '</main>'
    );

    bindHeaderEvents();

    var createBtn = document.getElementById('create-tag-btn');
    var nameInput = document.getElementById('tag-name-input');

    if (createBtn) {
      createBtn.addEventListener('click', function () { createTag(); });
    }
    if (nameInput) {
      nameInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') createTag();
      });
    }

    await loadTags();
  }

  async function loadTags() {
    var loadingEl = document.getElementById('tags-loading');
    try {
      state.tags = await api.listTags();
      renderTagsList();
    } catch (e) {
      toast.error('Failed to load tags: ' + e.message);
    } finally {
      if (loadingEl) loadingEl.style.display = 'none';
    }
  }

  function renderTagsList() {
    var listEl = document.getElementById('tags-list');
    if (!listEl) return;

    if (state.tags.length === 0) {
      listEl.innerHTML =
        '<div class="empty-state">' +
          '<div class="empty-state-title">No tags yet</div>' +
          '<div class="empty-state-text">Create a tag to organize your articles.</div>' +
        '</div>';
      return;
    }

    listEl.innerHTML = state.tags.map(function (t) {
      return (
        '<div class="tag-row">' +
          '<a href="#/?tag=' + encodeURIComponent(t.id) + '" class="tag-row-name">' + escapeHtml(t.name) + '</a>' +
          '<div class="tag-row-actions">' +
            '<button class="btn btn-sm btn-danger delete-tag-btn" data-id="' + escapeHtml(t.id) + '">Delete</button>' +
          '</div>' +
        '</div>'
      );
    }).join('');

    listEl.querySelectorAll('.delete-tag-btn').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        if (!confirm('Delete this tag?')) return;
        try {
          await api.deleteTag(btn.dataset.id);
          state.tags = state.tags.filter(function (t) { return t.id !== btn.dataset.id; });
          renderTagsList();
          toast.success('Tag deleted');
        } catch (e) {
          toast.error(e.message);
        }
      });
    });
  }

  async function createTag() {
    var input = document.getElementById('tag-name-input');
    var name = input ? input.value.trim() : '';
    if (!name) {
      toast.error('Enter a tag name');
      return;
    }
    try {
      var tag = await api.createTag(name);
      state.tags.push(tag);
      state.tags.sort(function (a, b) { return a.name.localeCompare(b.name); });
      renderTagsList();
      if (input) input.value = '';
      toast.success('Tag created');
    } catch (e) {
      toast.error(e.message);
    }
  }

  // =========================================================================
  // View: Tag-filtered article list
  // =========================================================================

  function renderTagFiltered() {
    // Extract tag param from hash: #/?tag=...
    var hash = router.currentHash();
    var match = hash.match(/[?&]tag=([^&]+)/);
    if (match) {
      state.filter = 'all';
      state.articles = [];
      state.offset = 0;
      state.hasMore = true;

      setContent(
        renderHeader() +
        '<main class="main-content">' +
          '<a href="#/tags" class="reader-back">\u2190 Back to tags</a>' +
          '<h2 class="section-title">Articles tagged</h2>' +
          '<div class="article-list" id="article-list"></div>' +
          '<div class="load-more" id="load-more" style="display:none">' +
            '<button class="btn btn-secondary" id="load-more-btn">Load more</button>' +
          '</div>' +
          '<div class="loading" id="articles-loading"><div class="spinner"></div></div>' +
        '</main>'
      );

      bindHeaderEvents();

      var loadMoreBtn = document.getElementById('load-more-btn');
      if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', function () {
          loadTagFilteredArticles(decodeURIComponent(match[1]));
        });
      }

      loadTagFilteredArticles(decodeURIComponent(match[1]));
      return true;
    }
    return false;
  }

  async function loadTagFilteredArticles(tagId) {
    if (state.loading || !state.hasMore) return;
    state.loading = true;

    var loadingEl = document.getElementById('articles-loading');
    var loadMoreEl = document.getElementById('load-more');
    if (loadingEl) loadingEl.style.display = 'flex';
    if (loadMoreEl) loadMoreEl.style.display = 'none';

    try {
      var articles = await api.listArticles({ tag: tagId, limit: state.limit, offset: state.offset });
      state.articles = state.articles.concat(articles);
      state.offset += articles.length;
      state.hasMore = articles.length >= state.limit;
      renderArticleCards();
    } catch (e) {
      toast.error('Failed to load articles: ' + e.message);
    } finally {
      state.loading = false;
      if (loadingEl) loadingEl.style.display = 'none';
      if (loadMoreEl) loadMoreEl.style.display = state.hasMore ? 'flex' : 'none';
    }
  }

  // =========================================================================
  // Initialization
  // =========================================================================

  async function init() {
    toast.init();
    audioPlayer.init();

    // Online/offline detection
    window.addEventListener('online', function () {
      state.online = true;
      var bar = document.getElementById('offline-bar');
      if (bar) bar.classList.remove('visible');
      // Trigger background sync
      if ('serviceWorker' in navigator && 'sync' in window.SyncManager) {
        navigator.serviceWorker.ready.then(function (reg) {
          return reg.sync.register('tasche-sync');
        }).catch(function () {});
      }
    });

    window.addEventListener('offline', function () {
      state.online = false;
      var bar = document.getElementById('offline-bar');
      if (bar) bar.classList.add('visible');
    });

    // Register routes
    router.register('/login', renderLogin);
    router.register('/', function () {
      // Check for tag filter in hash
      if (!renderTagFiltered()) {
        renderArticleList();
      }
    });
    router.register('/article/:id', renderReader);
    router.register('/search', renderSearch);
    router.register('/tags', renderTags);

    // Listen for hash changes
    window.addEventListener('hashchange', function () {
      router.resolve();
    });

    // Check session
    try {
      var user = await api.getSession();
      state.user = user;

      // Handle Web Share Target (URL passed as query param)
      var urlParams = new URLSearchParams(window.location.search);
      var sharedUrl = urlParams.get('url');
      if (sharedUrl) {
        var sharedTitle = urlParams.get('title') || '';
        api.createArticle(sharedUrl, sharedTitle)
          .then(function () { toast.success('Article saved!'); })
          .catch(function (e2) { toast.error('Save failed: ' + e2.message); });
        // Clean the URL params
        window.history.replaceState({}, '', window.location.pathname + window.location.hash);
      }

      router.resolve();
    } catch (e) {
      state.user = null;
      renderLogin();
    }
  }

  // Start the app when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
