import { useState, useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { addToast } from '../state.js';
import { IconEye, IconShuffle, IconBookOpen } from '../components/Icons.jsx';
import { getRandomHighlight } from '../api.js';
import { HIGHLIGHT_CSS } from '../constants.js';

export function Review() {
  var [highlight, setHighlight] = useState(null);
  var [loading, setLoading] = useState(true);
  var [revealed, setRevealed] = useState(false);
  var [empty, setEmpty] = useState(false);

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
          <LoadingSpinner />
        </main>
      </>
    );
  }

  if (empty) {
    return (
      <>
        <Header />
        <main class="main-content">
          <EmptyState icon={IconBookOpen} title="No highlights to review">
            Create highlights in the Reader view to start reviewing them.
          </EmptyState>
        </main>
      </>
    );
  }

  if (!highlight) {
    return (
      <>
        <Header />
        <main class="main-content">
          <EmptyState title="Could not load highlight">
            <button class="btn btn-primary" onClick={handleNext} style={{ marginTop: '16px' }}>
              Try again
            </button>
          </EmptyState>
        </main>
      </>
    );
  }

  var colorBg = HIGHLIGHT_CSS[highlight.color] || HIGHLIGHT_CSS.yellow;

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
