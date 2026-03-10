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

vi.mock('../../../src/nav.js', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    nav: {
      article: vi.fn(),
      search: vi.fn(),
      library: vi.fn(),
      tagFilter: vi.fn(),
      clearTagFilter: vi.fn(),
    },
  };
});

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
  IconSearch: () => <span>Search</span>,
}));

vi.mock('../../../src/hooks/useKeyboardShortcuts.js', () => ({
  useKeyboardShortcuts: vi.fn(),
}));

// Partial mock: real signals, mock only side-effectful functions
vi.mock('../../../src/state.js', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    addToast: vi.fn(),
  };
});

// Real utils — formatDate is a pure function that works in jsdom

import { createArticle } from '../../../src/api.js';
import { addToast, tags as tagsSignal } from '../../../src/state.js';

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

  // ── Multi-tag filter bar ──

  it('shows tag filter bar when tags prop is non-empty', () => {
    render(<Library tags={['tag-1']} />);
    expect(screen.getByText('Articles tagged')).toBeInTheDocument();
    expect(screen.getByTitle('Remove tag filter tag-1')).toBeInTheDocument();
  });

  it('does not show tag filter bar when tags is empty', () => {
    render(<Library tags={[]} />);
    expect(screen.queryByText('Articles tagged')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('Paste a URL to save...')).toBeInTheDocument();
  });

  it('does not show tag filter bar when tags is undefined', () => {
    render(<Library />);
    expect(screen.queryByText('Articles tagged')).not.toBeInTheDocument();
  });

  it('shows clear all button when multiple tags active', () => {
    render(<Library tags={['tag-1', 'tag-2']} />);
    expect(screen.getByText('Clear all')).toBeInTheDocument();
  });

  it('does not show clear all for single tag', () => {
    render(<Library tags={['tag-1']} />);
    expect(screen.queryByText('Clear all')).not.toBeInTheDocument();
  });

  it('shows remove button for each active tag (falls back to ID when names unavailable)', () => {
    tagsSignal.value = [];
    render(<Library tags={['tag-1', 'tag-2', 'tag-3']} />);
    expect(screen.getByTitle('Remove tag filter tag-1')).toBeInTheDocument();
    expect(screen.getByTitle('Remove tag filter tag-2')).toBeInTheDocument();
    expect(screen.getByTitle('Remove tag filter tag-3')).toBeInTheDocument();
  });

  // ── Tag name resolution ──

  it('displays tag names instead of IDs when tags signal has data', () => {
    tagsSignal.value = [
      { id: 'tag-1', name: 'python' },
      { id: 'tag-2', name: 'rust' },
    ];
    render(<Library tags={['tag-1', 'tag-2']} />);
    expect(screen.getByText('python')).toBeInTheDocument();
    expect(screen.getByText('rust')).toBeInTheDocument();
    expect(screen.getByTitle('Remove tag filter python')).toBeInTheDocument();
    expect(screen.getByTitle('Remove tag filter rust')).toBeInTheDocument();

    tagsSignal.value = [];
  });

  it('falls back to tag ID when tag is not in global tags signal', () => {
    tagsSignal.value = [{ id: 'tag-1', name: 'python' }];
    render(<Library tags={['tag-1', 'unknown-tag']} />);
    expect(screen.getByText('python')).toBeInTheDocument();
    expect(screen.getByText('unknown-tag')).toBeInTheDocument();

    tagsSignal.value = [];
  });

  it('passes tags array to listArticles', async () => {
    const { listArticles } = await import('../../../src/api.js');
    render(<Library tags={['tag-1', 'tag-2']} />);
    await waitFor(() => {
      expect(listArticles).toHaveBeenCalledWith(
        expect.objectContaining({ tag: ['tag-1', 'tag-2'] }),
      );
    });
  });
});
