import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { Reader } from '../../../src/views/Reader.jsx';

vi.mock('../../../src/api.js', () => ({
  getArticle: vi.fn(() =>
    Promise.resolve({
      id: 'art-1',
      title: 'Test Article',
      domain: 'example.com',
      original_url: 'https://example.com/test',
      excerpt: 'An excerpt',
      reading_status: 'unread',
      reading_time_minutes: 5,
      is_favorite: 0,
      audio_status: null,
      status: 'ready',
      original_status: 'unknown',
      scroll_position: 0,
      word_count: 1000,
      markdown_content: null,
    }),
  ),
  getArticleContent: vi.fn(() => Promise.resolve('<p>Article content</p>')),
  getArticleMarkdown: vi.fn(() => Promise.resolve('')),
  updateArticle: vi.fn(() => Promise.resolve()),
  deleteArticle: vi.fn(() => Promise.resolve()),
  listenLater: vi.fn(() => Promise.resolve()),
  retryArticle: vi.fn(() => Promise.resolve()),
  checkOriginal: vi.fn(() => Promise.resolve({ original_status: 'available' })),
  saveForOffline: vi.fn(),
  saveAudioOffline: vi.fn(),
  isOfflineCached: vi.fn(() =>
    Promise.resolve({ cached: false, hasContent: false, hasAudio: false }),
  ),
  getAudioTiming: vi.fn(() => Promise.resolve(null)),
}));

vi.mock('../../../src/nav.js', () => ({
  nav: {
    article: vi.fn(),
    library: vi.fn(),
    search: vi.fn(),
    tagFilter: vi.fn(),
  },
}));

vi.mock('../../../src/components/Header.jsx', () => ({
  Header: () => <div data-testid="header">Header</div>,
}));

vi.mock('../../../src/components/EmptyState.jsx', () => ({
  EmptyState: ({ children, title }) => (
    <div data-testid="empty-state">
      {title}: {children}
    </div>
  ),
  LoadingSpinner: () => <div data-testid="loading-spinner">Loading...</div>,
}));

vi.mock('../../../src/components/TagPicker.jsx', () => ({
  TagPicker: () => <div data-testid="tag-picker">TagPicker</div>,
}));

vi.mock('../../../src/components/ReaderToolbar.jsx', () => ({
  ReaderToolbar: () => null,
}));

vi.mock('../../../src/components/AudioPlayer.jsx', () => ({
  playAudio: vi.fn(),
  audioState: { value: { articleId: null, articleTitle: '', isPlaying: false, visible: false } },
  getAudio: vi.fn(),
}));

vi.mock('../../../src/hooks/useKeyboardShortcuts.js', () => ({
  useKeyboardShortcuts: vi.fn(),
}));

vi.mock('../../../src/hooks/useSWMessage.js', () => ({
  useSWMessage: vi.fn(),
}));

vi.mock('../../../src/state.js', () => ({
  articles: { value: [] },
  addToast: vi.fn(),
}));

vi.mock('../../../src/readerPrefs.js', () => ({
  readerPrefs: { value: { contentMode: 'html', theme: 'auto' } },
  getReaderStyle: vi.fn(() => ({})),
  updatePref: vi.fn(),
}));

vi.mock('../../../src/markdown.js', () => ({
  renderMarkdown: vi.fn((md) => '<p>' + md + '</p>'),
}));

vi.mock('../../../src/utils.js', () => ({
  escapeHtml: vi.fn((s) => s),
}));

vi.mock('dompurify', () => ({
  default: {
    sanitize: vi.fn((html) => html),
  },
}));

import { listenLater, deleteArticle, retryArticle, checkOriginal } from '../../../src/api.js';
import { addToast } from '../../../src/state.js';
import { nav } from '../../../src/nav.js';

describe('Reader', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders article title and metadata', async () => {
    render(<Reader id="art-1" />);
    await waitFor(() => {
      expect(screen.getByText('Test Article')).toBeInTheDocument();
    });
    expect(screen.getByText('example.com')).toBeInTheDocument();
    expect(screen.getByText('5 min read')).toBeInTheDocument();
  });

  it('disables Listen Later button while loading', async () => {
    const user = userEvent.setup();
    listenLater.mockImplementation(() => new Promise(() => {})); // never resolves

    render(<Reader id="art-1" />);
    await waitFor(() => screen.getByText('Listen Later'));

    await user.click(screen.getByText('Listen Later'));

    await waitFor(() => {
      expect(screen.getByText('Requesting...')).toBeInTheDocument();
      expect(screen.getByText('Requesting...').closest('button')).toBeDisabled();
    });
  });

  it('disables Delete button while deleting', async () => {
    const user = userEvent.setup();
    deleteArticle.mockImplementation(() => new Promise(() => {}));

    render(<Reader id="art-1" />);
    await waitFor(() => screen.getByText('Delete'));

    await user.click(screen.getByText('Delete'));

    await waitFor(() => {
      expect(screen.getByText('Deleting...')).toBeInTheDocument();
      expect(screen.getByText('Deleting...').closest('button')).toBeDisabled();
    });
  });

  it('navigates to library after successful delete', async () => {
    const user = userEvent.setup();
    deleteArticle.mockResolvedValueOnce();

    render(<Reader id="art-1" />);
    await waitFor(() => screen.getByText('Delete'));

    await user.click(screen.getByText('Delete'));

    await waitFor(() => {
      expect(nav.library).toHaveBeenCalled();
    });
  });

  it('disables Retry button while retrying', async () => {
    const user = userEvent.setup();
    retryArticle.mockImplementation(() => new Promise(() => {}));

    render(<Reader id="art-1" />);
    await waitFor(() => screen.getByText('Retry'));

    await user.click(screen.getByText('Retry'));

    await waitFor(() => {
      expect(screen.getByText('Retrying...')).toBeInTheDocument();
      expect(screen.getByText('Retrying...').closest('button')).toBeDisabled();
    });
  });

  it('disables Check now button while checking original', async () => {
    const user = userEvent.setup();
    checkOriginal.mockImplementation(() => new Promise(() => {}));

    render(<Reader id="art-1" />);
    await waitFor(() => screen.getByText('Check now'));

    await user.click(screen.getByText('Check now'));

    await waitFor(() => {
      expect(screen.getByText('Checking...')).toBeInTheDocument();
      expect(screen.getByText('Checking...').closest('button')).toBeDisabled();
    });
  });

  it('shows success toast on listen later', async () => {
    const user = userEvent.setup();
    listenLater.mockResolvedValueOnce();

    render(<Reader id="art-1" />);
    await waitFor(() => screen.getByText('Listen Later'));

    await user.click(screen.getByText('Listen Later'));

    await waitFor(() => {
      expect(addToast).toHaveBeenCalledWith('Audio generation queued', 'success');
    });
  });

  it('renders Favourite button', async () => {
    render(<Reader id="art-1" />);
    await waitFor(() => {
      expect(screen.getByText('Favourite')).toBeInTheDocument();
    });
  });
});
