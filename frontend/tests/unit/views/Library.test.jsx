import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { Library } from '../../../src/views/Library.jsx';

vi.mock('../../../src/api.js', () => ({
  listArticles: vi.fn(() => Promise.resolve([])),
  createArticle: vi.fn(() => Promise.resolve({ id: 'new-1' })),
  batchUpdateArticles: vi.fn(() => Promise.resolve()),
  batchDeleteArticles: vi.fn(() => Promise.resolve()),
  cacheArticlesForOffline: vi.fn(),
  queueOfflineMutation: vi.fn(),
}));

vi.mock('../../../src/articleActions.js', () => ({
  toggleArchive: vi.fn(),
  toggleFavorite: vi.fn(),
  removeArticle: vi.fn(() => Promise.resolve(true)),
}));

vi.mock('../../../src/nav.js', () => ({
  nav: {
    article: vi.fn(),
    search: vi.fn(),
    library: vi.fn(),
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

vi.mock('../../../src/components/ArticleCard.jsx', () => ({
  ArticleCard: ({ article }) => <div data-testid="article-card">{article.title}</div>,
}));

vi.mock('../../../src/components/Pagination.jsx', () => ({
  Pagination: () => null,
}));

vi.mock('../../../src/components/Icons.jsx', () => ({
  IconBookOpen: () => <span>BookOpen</span>,
  IconHeadphones: () => <span>Headphones</span>,
  IconSelectMode: () => <span>Select</span>,
  IconArchive: () => <span>Archive</span>,
  IconTrash: () => <span>Trash</span>,
  IconX: () => <span>X</span>,
}));

vi.mock('../../../src/hooks/useKeyboardShortcuts.js', () => ({
  useKeyboardShortcuts: vi.fn(),
}));

vi.mock('../../../src/state.js', () => ({
  articles: { value: [] },
  filter: { value: 'unread' },
  offset: { value: 0 },
  hasMore: { value: true },
  loading: { value: false },
  isOffline: { value: false },
  addToast: vi.fn(),
  limit: { value: 20 },
  showShortcuts: { value: false },
}));

vi.mock('../../../src/utils.js', () => ({
  formatDate: vi.fn(() => '2d ago'),
}));

import { createArticle } from '../../../src/api.js';
import { addToast } from '../../../src/state.js';

describe('Library', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders save form with URL input and both save buttons', () => {
    render(<Library />);
    expect(screen.getByPlaceholderText('Paste a URL to save...')).toBeInTheDocument();
    expect(screen.getByText('Save')).toBeInTheDocument();
    expect(screen.getByText('Save audio')).toBeInTheDocument();
  });

  it('shows error toast on empty URL save', async () => {
    const user = userEvent.setup();
    render(<Library />);

    await user.click(screen.getByText('Save'));

    expect(addToast).toHaveBeenCalledWith('Please enter a URL', 'error');
  });

  it('disables Save button while saving', async () => {
    const user = userEvent.setup();
    createArticle.mockImplementation(() => new Promise(() => {})); // never resolves

    render(<Library />);

    const input = screen.getByPlaceholderText('Paste a URL to save...');
    await user.type(input, 'https://example.com');
    await user.click(screen.getByText('Save'));

    expect(screen.getByText('Saving...')).toBeDisabled();
  });

  it('re-enables Save button after successful save', async () => {
    const user = userEvent.setup();
    createArticle.mockResolvedValueOnce({ id: 'new-1' });

    render(<Library />);

    const input = screen.getByPlaceholderText('Paste a URL to save...');
    await user.type(input, 'https://example.com');
    await user.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(screen.getByText('Save')).not.toBeDisabled();
    });
  });

  it('shows success toast after save', async () => {
    const user = userEvent.setup();
    createArticle.mockResolvedValueOnce({ id: 'new-1' });

    render(<Library />);

    const input = screen.getByPlaceholderText('Paste a URL to save...');
    await user.type(input, 'https://example.com');
    await user.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(addToast).toHaveBeenCalledWith('Article saved!', 'success');
    });
  });

  it('shows info toast for duplicate article', async () => {
    const user = userEvent.setup();
    createArticle.mockResolvedValueOnce({
      id: 'dup-1',
      updated: true,
      created_at: '2025-01-01',
    });

    render(<Library />);

    const input = screen.getByPlaceholderText('Paste a URL to save...');
    await user.type(input, 'https://example.com');
    await user.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(addToast).toHaveBeenCalledWith(expect.stringContaining('already added'), 'info');
    });
  });

  it('Save audio button calls createArticle with listen_later true', async () => {
    const user = userEvent.setup();
    createArticle.mockResolvedValueOnce({ id: 'new-1' });

    render(<Library />);

    const input = screen.getByPlaceholderText('Paste a URL to save...');
    await user.type(input, 'https://example.com');
    await user.click(screen.getByText('Save audio'));

    await waitFor(() => {
      expect(createArticle).toHaveBeenCalledWith('https://example.com', null, true);
      expect(addToast).toHaveBeenCalledWith('Article saved! Audio will be generated.', 'success');
    });
  });

  it('disables both buttons while Save audio is in progress', async () => {
    const user = userEvent.setup();
    createArticle.mockImplementation(() => new Promise(() => {})); // never resolves

    render(<Library />);

    const input = screen.getByPlaceholderText('Paste a URL to save...');
    await user.type(input, 'https://example.com');
    await user.click(screen.getByText('Save audio'));

    expect(screen.getByText('Save')).toBeDisabled();
    expect(screen.getByText('Saving...')).toBeDisabled();
  });

  it('renders filter tabs', () => {
    render(<Library />);
    expect(screen.getByText('Unread')).toBeInTheDocument();
    expect(screen.getByText('Audio')).toBeInTheDocument();
    expect(screen.getByText('Favourites')).toBeInTheDocument();
    expect(screen.getByText('Archived')).toBeInTheDocument();
  });

  it('renders sort select', () => {
    render(<Library />);
    expect(screen.getByText('Newest first')).toBeInTheDocument();
  });
});
