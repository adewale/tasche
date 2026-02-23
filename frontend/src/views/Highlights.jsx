import { useState, useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { addToast } from '../state.js';
import { IconTrash, IconBookOpen } from '../components/Icons.jsx';
import { getAllHighlights, deleteHighlight as apiDeleteHighlight } from '../api.js';
import { HIGHLIGHT_CSS } from '../constants.js';

export function Highlights() {
  var [highlights, setHighlights] = useState([]);
  var [loading, setLoading] = useState(true);
  var [offset, setOffset] = useState(0);
  var [hasMore, setHasMore] = useState(true);

  useEffect(function () {
    loadHighlights(0, true);
  }, []);

  function loadHighlights(newOffset, reset) {
    setLoading(true);
    getAllHighlights(50, newOffset)
      .then(function (data) {
        if (reset) {
          setHighlights(data);
        } else {
          setHighlights(function (prev) { return prev.concat(data); });
        }
        setHasMore(data.length === 50);
        setOffset(newOffset + data.length);
        setLoading(false);
      })
      .catch(function (e) {
        addToast(e.message, 'error');
        setLoading(false);
      });
  }

  function handleLoadMore() {
    loadHighlights(offset, false);
  }

  function handleDelete(id) {
    if (!confirm('Delete this highlight?')) return;
    apiDeleteHighlight(id)
      .then(function () {
        setHighlights(function (prev) { return prev.filter(function (h) { return h.id !== id; }); });
        addToast('Highlight deleted', 'success');
      })
      .catch(function (e) {
        addToast(e.message, 'error');
      });
  }

  // Group highlights by article
  var grouped = {};
  var order = [];
  highlights.forEach(function (h) {
    if (!grouped[h.article_id]) {
      grouped[h.article_id] = {
        article_title: h.article_title || 'Untitled',
        article_id: h.article_id,
        items: [],
      };
      order.push(h.article_id);
    }
    grouped[h.article_id].items.push(h);
  });

  return (
    <>
      <Header />
      <main class="main-content">
        <h1 class="section-title">Highlights</h1>

        {!loading && highlights.length === 0 && (
          <EmptyState icon={IconBookOpen} title="No highlights yet">
            Select text in the Reader view to create highlights.
          </EmptyState>
        )}

        <div class="highlights-list">
          {order.map(function (articleId) {
            var group = grouped[articleId];
            return (
              <div key={articleId} class="highlights-group">
                <h2 class="highlights-group-title">
                  <a href={'#/article/' + articleId}>{group.article_title}</a>
                </h2>
                {group.items.map(function (h) {
                  return (
                    <div key={h.id} class="highlight-card" data-color={h.color}>
                      <div
                        class="highlight-card-bar"
                        style={{ background: HIGHLIGHT_CSS[h.color] || HIGHLIGHT_CSS.yellow }}
                      ></div>
                      <div class="highlight-card-body">
                        <blockquote class="highlight-card-text">{h.text}</blockquote>
                        {h.note && (
                          <div class="highlight-card-note">{h.note}</div>
                        )}
                        <div class="highlight-card-footer">
                          <span class="highlight-card-date">
                            {new Date(h.created_at).toLocaleDateString()}
                          </span>
                          <button
                            class="btn btn-sm btn-secondary"
                            onClick={function () { handleDelete(h.id); }}
                          >
                            <IconTrash size={12} /> Delete
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>

        {loading && <LoadingSpinner />}

        {!loading && hasMore && highlights.length > 0 && (
          <div class="load-more">
            <button class="btn btn-secondary" onClick={handleLoadMore}>
              Load more
            </button>
          </div>
        )}
      </main>
    </>
  );
}
