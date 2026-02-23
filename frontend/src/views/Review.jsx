import { useState, useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { addToast } from '../state.js';
import { IconEye, IconShuffle, IconBookOpen } from '../components/Icons.jsx';
import { getRandomHighlight } from '../api.js';

var HIGHLIGHT_COLORS = {
  yellow: 'var(--highlight-yellow)',
  green: 'var(--highlight-green)',
  blue: 'var(--highlight-blue)',
  pink: 'var(--highlight-pink)',
};

export function Review() {
  var _highlight = useState(null);
  var highlight = _highlight[0];
  var setHighlight = _highlight[1];
  var _loading = useState(true);
  var loading = _loading[0];
  var setLoading = _loading[1];
  var _revealed = useState(false);
  var revealed = _revealed[0];
  var setRevealed = _revealed[1];
  var _empty = useState(false);
  var empty = _empty[0];
  var setEmpty = _empty[1];

  useEffect(function () {
    loadRandom();
  }, []);

  function loadRandom() {
    setLoading(true);
    setRevealed(false);
    setEmpty(false);
    getRandomHighlight()
      .then(function (data) {
        setHighlight(data);
        setLoading(false);
      })
      .catch(function (e) {
        if (e.status === 404) {
          setEmpty(true);
        } else {
          addToast(e.message, 'error');
        }
        setHighlight(null);
        setLoading(false);
      });
  }

  function handleReveal() {
    setRevealed(true);
  }

  function handleNext() {
    loadRandom();
  }

  if (loading) {
    return (
      <>
        <Header />
        <main class="main-content">
          <div class="loading">
            <div class="spinner"></div>
          </div>
        </main>
      </>
    );
  }

  if (empty) {
    return (
      <>
        <Header />
        <main class="main-content">
          <div class="empty-state">
            <div class="empty-state-icon">
              <IconBookOpen />
            </div>
            <div class="empty-state-title">No highlights to review</div>
            <div class="empty-state-text">
              Create highlights in the Reader view to start reviewing them.
            </div>
          </div>
        </main>
      </>
    );
  }

  if (!highlight) {
    return (
      <>
        <Header />
        <main class="main-content">
          <div class="empty-state">
            <div class="empty-state-title">Could not load highlight</div>
            <button class="btn btn-primary" onClick={handleNext} style={{ marginTop: '16px' }}>
              Try again
            </button>
          </div>
        </main>
      </>
    );
  }

  var colorBg = HIGHLIGHT_COLORS[highlight.color] || HIGHLIGHT_COLORS.yellow;

  return (
    <>
      <Header />
      <main class="main-content">
        <div class="review-container">
          <h1 class="section-title">Review</h1>
          <p class="review-prompt">Do you remember the context of this highlight?</p>

          <div class="review-card">
            <div class="review-card-bar" style={{ background: colorBg }}></div>
            <blockquote class="review-card-text">{highlight.text}</blockquote>

            {highlight.note && (
              <div class="review-card-note">
                <strong>Note:</strong> {highlight.note}
              </div>
            )}

            {!revealed && (
              <button class="btn btn-primary review-reveal-btn" onClick={handleReveal}>
                <IconEye size={16} /> Reveal context
              </button>
            )}

            {revealed && (
              <div class="review-reveal">
                <div class="review-reveal-article">
                  <span class="review-reveal-label">From:</span>{' '}
                  <a href={'#/article/' + highlight.article_id}>
                    {highlight.article_title || 'Untitled'}
                  </a>
                </div>
                {(highlight.prefix || highlight.suffix) && (
                  <div class="review-reveal-context">
                    {highlight.prefix && (
                      <span class="review-context-prefix">...{highlight.prefix}</span>
                    )}
                    <mark style={{ background: colorBg }}>{highlight.text}</mark>
                    {highlight.suffix && (
                      <span class="review-context-suffix">{highlight.suffix}...</span>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          <div class="review-actions">
            <button class="btn btn-primary" onClick={handleNext}>
              <IconShuffle size={16} /> Next highlight
            </button>
          </div>
        </div>
      </main>
    </>
  );
}
