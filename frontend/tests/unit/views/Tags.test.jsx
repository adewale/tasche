import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { Tags } from '../../../src/views/Tags.jsx';

vi.mock('../../../src/api.js', () => ({
  listTags: vi.fn(() => Promise.resolve([])),
  createTag: vi.fn(() =>
    Promise.resolve({
      id: 'tag-new',
      user_id: 'u1',
      name: 'NewTag',
      created_at: '2025-01-01T00:00:00Z',
    }),
  ),
  deleteTag: vi.fn(() => Promise.resolve()),
  renameTag: vi.fn(() => Promise.resolve({ id: 'tag-1', user_id: 'u1', name: 'Renamed' })),
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

vi.mock('../../../src/components/Icons.jsx', () => ({
  IconPencil: () => <span>Pencil</span>,
}));

vi.mock('../../../src/state.js', () => ({
  tags: { value: [] },
  addToast: vi.fn(),
}));

import { listTags, createTag, deleteTag } from '../../../src/api.js';
import { tags as tagsSignal, addToast } from '../../../src/state.js';

describe('Tags', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    tagsSignal.value = [];
  });

  it('renders create tag form', () => {
    render(<Tags />);
    expect(screen.getByPlaceholderText('New tag name...')).toBeInTheDocument();
    expect(screen.getByText('Create Tag')).toBeInTheDocument();
  });

  it('shows error toast for empty tag name', async () => {
    const user = userEvent.setup();
    render(<Tags />);

    await user.click(screen.getByText('Create Tag'));

    expect(addToast).toHaveBeenCalledWith('Enter a tag name', 'error');
  });

  it('disables Create Tag button while creating', async () => {
    const user = userEvent.setup();
    createTag.mockImplementation(() => new Promise(() => {})); // never resolves

    render(<Tags />);

    const input = screen.getByPlaceholderText('New tag name...');
    await user.type(input, 'MyTag');
    await user.click(screen.getByText('Create Tag'));

    expect(screen.getByText('Creating...')).toBeDisabled();
  });

  it('disables Delete button while deleting a tag', async () => {
    const user = userEvent.setup();
    listTags.mockResolvedValueOnce([{ id: 'tag-1', name: 'JavaScript', article_count: 3 }]);
    deleteTag.mockImplementation(() => new Promise(() => {})); // never resolves

    render(<Tags />);

    await waitFor(() => screen.getByText('Delete'));
    await user.click(screen.getByText('Delete'));

    expect(screen.getByText('Deleting...')).toBeDisabled();
  });

  it('shows success toast on tag creation', async () => {
    const user = userEvent.setup();
    createTag.mockResolvedValueOnce({ id: 'tag-new', name: 'NewTag', article_count: 0 });

    render(<Tags />);

    const input = screen.getByPlaceholderText('New tag name...');
    await user.type(input, 'NewTag');
    await user.click(screen.getByText('Create Tag'));

    await waitFor(() => {
      expect(addToast).toHaveBeenCalledWith('Tag created', 'success');
    });
  });
});
