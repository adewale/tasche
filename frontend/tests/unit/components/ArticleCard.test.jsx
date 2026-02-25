import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { ArticleCard } from '../../../src/components/ArticleCard.jsx';

// Mock dependencies
vi.mock('../../../src/api.js', () => ({
  getArticleTags: vi.fn(() => Promise.resolve([])),
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

vi.mock('../../../src/components/AudioPlayer.jsx', () => ({
  playAudio: vi.fn(),
}));

vi.mock('../../../src/state.js', () => ({
  articles: { value: [] },
  addToast: vi.fn(),
}));

vi.mock('../../../src/utils.js', () => ({
  formatDate: vi.fn(() => '2d ago'),
}));

import { listenLater } from '../../../src/api.js';
import { addToast } from '../../../src/state.js';

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

  it('shows pending icon when audio is generating', () => {
    render(<ArticleCard article={makeArticle({ audio_status: 'pending' })} />);
    expect(screen.getByTitle('Generating audio...')).toBeDisabled();
  });

  it('hides listen later button when audio is ready', () => {
    render(<ArticleCard article={makeArticle({ audio_status: 'ready' })} />);
    expect(screen.queryByTitle('Listen later')).not.toBeInTheDocument();
  });

  it('shows processing overlay for pending articles', () => {
    render(<ArticleCard article={makeArticle({ status: 'pending' })} />);
    expect(screen.getByText('Saving...')).toBeInTheDocument();
  });

  it('shows processing overlay for processing articles', () => {
    render(<ArticleCard article={makeArticle({ status: 'processing' })} />);
    expect(screen.getByText('Processing...')).toBeInTheDocument();
  });

  it('renders excerpt', () => {
    render(<ArticleCard article={makeArticle()} />);
    expect(screen.getByText('A test excerpt')).toBeInTheDocument();
  });
});
