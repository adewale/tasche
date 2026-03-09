import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { ArticleCard } from '../../../src/components/ArticleCard.jsx';

// Mock dependencies
vi.mock('../../../src/api.js', () => ({
  getArticleTags: vi.fn(() => Promise.resolve([])),
  getArticle: vi.fn(() => Promise.resolve({})),
  listenLater: vi.fn(() => Promise.resolve()),
  isOfflineCached: vi.fn(() =>
    Promise.resolve({ cached: false, hasContent: false, hasAudio: false }),
  ),
}));

vi.mock('../../../src/articleActions.js', () => ({
  toggleArchive: vi.fn(),
  toggleFavorite: vi.fn(),
  removeArticle: vi.fn(() => Promise.resolve(true)),
}));

vi.mock('../../../src/nav.js', () => ({
  nav: {
    article: vi.fn(),
    tagFilter: vi.fn(),
    library: vi.fn(),
  },
}));

vi.mock('../../../src/components/AudioPlayer.jsx', () => {
  const { signal } = require('@preact/signals');
  return {
    playAudio: vi.fn(),
    audioState: signal({ articleId: null, articleTitle: '', isPlaying: false, visible: false }),
  };
});

vi.mock('../../../src/state.js', () => ({
  articles: { value: [] },
  addToast: vi.fn(),
  pollAudioStatus: vi.fn(),
  pollArticleStatus: vi.fn(),
}));

vi.mock('../../../src/utils.js', () => ({
  formatDate: vi.fn(() => '2d ago'),
}));

import { listenLater } from '../../../src/api.js';
import { addToast } from '../../../src/state.js';
import { audioState } from '../../../src/components/AudioPlayer.jsx';

function makeArticle(overrides = {}) {
  return {
    id: 'art-1',
    title: 'Test Article',
    domain: 'example.com',
    original_url: 'https://example.com/test',
    excerpt: 'A test excerpt',
    reading_time_minutes: 5,
    reading_status: 'unread',
    reading_progress: 0,
    is_favorite: 0,
    audio_status: null,
    status: 'ready',
    created_at: '2025-01-01T00:00:00Z',
    thumbnail_key: null,
    ...overrides,
  };
}

describe('ArticleCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders article title and metadata', () => {
    render(<ArticleCard article={makeArticle()} />);
    expect(screen.getByText('Test Article')).toBeInTheDocument();
    expect(screen.getByText('example.com')).toBeInTheDocument();
    expect(screen.getByText('5 min read')).toBeInTheDocument();
  });

  it('shows listen later button when no audio', () => {
    render(<ArticleCard article={makeArticle()} />);
    expect(screen.getByTitle('Listen later')).toBeInTheDocument();
  });

  it('disables listen later button during audioLoading', async () => {
    const user = userEvent.setup();
    listenLater.mockImplementation(() => new Promise(() => {})); // never resolves

    render(<ArticleCard article={makeArticle()} />);
    const btn = screen.getByTitle('Listen later');
    expect(btn).not.toBeDisabled();

    await user.click(btn);
    expect(btn).toBeDisabled();
  });

  it('shows toast on successful listen later', async () => {
    const user = userEvent.setup();
    listenLater.mockResolvedValueOnce();

    render(<ArticleCard article={makeArticle()} />);
    await user.click(screen.getByTitle('Listen later'));

    await waitFor(() => {
      expect(addToast).toHaveBeenCalledWith('Audio generation queued', 'success');
    });
  });

  it('shows info toast on 409 conflict', async () => {
    const user = userEvent.setup();
    const err = new Error('Conflict');
    err.status = 409;
    listenLater.mockRejectedValueOnce(err);

    render(<ArticleCard article={makeArticle()} />);
    await user.click(screen.getByTitle('Listen later'));

    await waitFor(() => {
      expect(addToast).toHaveBeenCalledWith('Audio generation is already in progress', 'info');
    });
  });

  it('shows play button when audio is ready', () => {
    render(<ArticleCard article={makeArticle({ audio_status: 'ready' })} />);
    expect(screen.getByTitle('Play audio')).toBeInTheDocument();
  });

  it('shows pending icon when audio is pending', () => {
    render(<ArticleCard article={makeArticle({ audio_status: 'pending' })} />);
    expect(screen.getByTitle('Generating audio...')).toBeDisabled();
  });

  it('shows retry button when audio is stuck generating', () => {
    render(<ArticleCard article={makeArticle({ audio_status: 'generating' })} />);
    expect(screen.getByTitle('Regenerate audio')).toBeInTheDocument();
    expect(screen.getByTitle('Regenerate audio')).not.toBeDisabled();
  });

  it('hides listen later button when audio is ready', () => {
    render(<ArticleCard article={makeArticle({ audio_status: 'ready' })} />);
    expect(screen.queryByTitle('Listen later')).not.toBeInTheDocument();
  });

  it('shows processing overlay for pending articles', () => {
    const { container } = render(<ArticleCard article={makeArticle({ status: 'pending' })} />);
    expect(container.querySelector('.processing-march')).toBeInTheDocument();
    expect(container.querySelector('.article-card--processing')).toBeInTheDocument();
  });

  it('shows processing overlay for processing articles', () => {
    const { container } = render(<ArticleCard article={makeArticle({ status: 'processing' })} />);
    expect(container.querySelector('.processing-march')).toBeInTheDocument();
    expect(container.querySelector('.article-card--processing')).toBeInTheDocument();
  });

  it('renders excerpt', () => {
    render(<ArticleCard article={makeArticle()} />);
    expect(screen.getByText('A test excerpt')).toBeInTheDocument();
  });

  // ── Processing state: button visibility ──

  it('hides star and delete when status is pending', () => {
    render(<ArticleCard article={makeArticle({ status: 'pending' })} />);
    expect(screen.queryByTitle('Toggle favourite')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Delete')).not.toBeInTheDocument();
  });

  it('hides star and delete when status is processing', () => {
    render(<ArticleCard article={makeArticle({ status: 'processing' })} />);
    expect(screen.queryByTitle('Toggle favourite')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Delete')).not.toBeInTheDocument();
  });

  it('shows star and delete when status is ready', () => {
    render(<ArticleCard article={makeArticle({ status: 'ready' })} />);
    expect(screen.getByTitle('Toggle favourite')).toBeInTheDocument();
    expect(screen.getByTitle('Delete')).toBeInTheDocument();
  });

  it('shows star and delete when status is failed', () => {
    render(<ArticleCard article={makeArticle({ status: 'failed' })} />);
    expect(screen.getByTitle('Toggle favourite')).toBeInTheDocument();
    expect(screen.getByTitle('Delete')).toBeInTheDocument();
  });

  it('hides audio buttons when processing', () => {
    render(<ArticleCard article={makeArticle({ status: 'pending' })} />);
    expect(screen.queryByTitle('Listen later')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Play audio')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Generating audio...')).not.toBeInTheDocument();
  });

  it('hides archive button when processing', () => {
    render(<ArticleCard article={makeArticle({ status: 'pending' })} />);
    expect(screen.queryByTitle('Archive')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Move to unread')).not.toBeInTheDocument();
  });

  it('shows no action buttons during processing', () => {
    const { container } = render(<ArticleCard article={makeArticle({ status: 'pending' })} />);
    var buttons = container.querySelectorAll('.article-card-actions button');
    expect(buttons.length).toBe(0);
  });

  it('shows all action buttons when ready', () => {
    const { container } = render(<ArticleCard article={makeArticle({ status: 'ready' })} />);
    var buttons = container.querySelectorAll('.article-card-actions button');
    // Listen later + Archive + Star + Delete = 4
    expect(buttons.length).toBe(4);
  });

  it('restores buttons when article transitions from processing to ready', () => {
    const { container, rerender } = render(
      <ArticleCard article={makeArticle({ status: 'processing' })} />,
    );
    expect(container.querySelectorAll('.article-card-actions button').length).toBe(0);

    rerender(<ArticleCard article={makeArticle({ status: 'ready' })} />);
    expect(screen.getByTitle('Toggle favourite')).toBeInTheDocument();
    expect(screen.getByTitle('Delete')).toBeInTheDocument();
    expect(screen.getByTitle('Archive')).toBeInTheDocument();
    expect(screen.getByTitle('Listen later')).toBeInTheDocument();
  });

  it('restores buttons when article transitions from pending to failed', () => {
    const { container, rerender } = render(
      <ArticleCard article={makeArticle({ status: 'pending' })} />,
    );
    expect(container.querySelectorAll('.article-card-actions button').length).toBe(0);

    rerender(<ArticleCard article={makeArticle({ status: 'failed' })} />);
    expect(screen.getByTitle('Toggle favourite')).toBeInTheDocument();
    expect(screen.getByTitle('Delete')).toBeInTheDocument();
  });

  // ── Now-playing sound bars indicator ──

  it('shows sound bars instead of play button when this article is playing', () => {
    audioState.value = { articleId: 'art-1', articleTitle: 'Test', isPlaying: true, visible: true };

    const { container } = render(
      <ArticleCard article={makeArticle({ audio_status: 'ready' })} />,
    );
    expect(screen.getByTitle('Now playing')).toBeInTheDocument();
    expect(screen.queryByTitle('Play audio')).not.toBeInTheDocument();
    expect(container.querySelector('.audio-playing')).toBeInTheDocument();
    expect(container.querySelector('.sound-bar')).toBeInTheDocument();

    audioState.value = { articleId: null, articleTitle: '', isPlaying: false, visible: false };
  });

  it('shows play button when a different article is playing', () => {
    audioState.value = {
      articleId: 'other-article',
      articleTitle: 'Other',
      isPlaying: true,
      visible: true,
    };

    render(<ArticleCard article={makeArticle({ audio_status: 'ready' })} />);
    expect(screen.getByTitle('Play audio')).toBeInTheDocument();
    expect(screen.queryByTitle('Now playing')).not.toBeInTheDocument();

    audioState.value = { articleId: null, articleTitle: '', isPlaying: false, visible: false };
  });

  it('shows play button when this article audio is paused', () => {
    audioState.value = { articleId: 'art-1', articleTitle: 'Test', isPlaying: false, visible: true };

    render(<ArticleCard article={makeArticle({ audio_status: 'ready' })} />);
    expect(screen.getByTitle('Play audio')).toBeInTheDocument();
    expect(screen.queryByTitle('Now playing')).not.toBeInTheDocument();

    audioState.value = { articleId: null, articleTitle: '', isPlaying: false, visible: false };
  });

  it('sound bars button is disabled and non-interactive', () => {
    audioState.value = { articleId: 'art-1', articleTitle: 'Test', isPlaying: true, visible: true };

    render(<ArticleCard article={makeArticle({ audio_status: 'ready' })} />);
    expect(screen.getByTitle('Now playing')).toBeDisabled();

    audioState.value = { articleId: null, articleTitle: '', isPlaying: false, visible: false };
  });
});
