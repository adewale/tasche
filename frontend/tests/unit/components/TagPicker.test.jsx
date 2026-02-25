import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { TagPicker } from '../../../src/components/TagPicker.jsx';

vi.mock('../../../src/api.js', () => ({
  listTags: vi.fn(() => Promise.resolve([])),
  getArticleTags: vi.fn(() => Promise.resolve([{ id: 'tag-1', name: 'JavaScript' }])),
  addArticleTag: vi.fn(() => Promise.resolve()),
  removeArticleTag: vi.fn(() => Promise.resolve()),
}));

vi.mock('../../../src/state.js', () => ({
  tags: { value: [{ id: 'tag-2', name: 'Python' }] },
  addToast: vi.fn(),
}));

import { addArticleTag, removeArticleTag } from '../../../src/api.js';
import { addToast } from '../../../src/state.js';

describe('TagPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders existing article tags', async () => {
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => {
      expect(screen.getByText('JavaScript')).toBeInTheDocument();
    });
  });

  it('shows + Tag button to open picker', async () => {
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => {
      expect(screen.getByText('+ Tag')).toBeInTheDocument();
    });
  });

  it('opens picker and shows tag select on click', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    expect(screen.getByText('Select a tag...')).toBeInTheDocument();
  });

  it('disables Add button while adding tag', async () => {
    const user = userEvent.setup();
    addArticleTag.mockImplementation(() => new Promise(() => {})); // never resolves

    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));
    await user.click(screen.getByText('+ Tag'));

    // Select a tag from dropdown
    const select = screen.getByRole('combobox');
    await user.selectOptions(select, 'tag-2');

    const addBtn = screen.getByText('Add');
    await user.click(addBtn);

    expect(screen.getByText('Adding...')).toBeDisabled();
  });

  it('shows "..." while removing a tag', async () => {
    const user = userEvent.setup();
    removeArticleTag.mockImplementation(() => new Promise(() => {})); // never resolves

    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('JavaScript'));

    // Click the remove button (the × next to the tag)
    const removeBtn = screen.getByText('\u00D7');
    await user.click(removeBtn);

    expect(screen.getByText('...')).toBeInTheDocument();
  });

  it('shows toast on successful tag add', async () => {
    const user = userEvent.setup();
    addArticleTag.mockResolvedValueOnce();

    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));
    await user.click(screen.getByText('+ Tag'));

    const select = screen.getByRole('combobox');
    await user.selectOptions(select, 'tag-2');
    await user.click(screen.getByText('Add'));

    await waitFor(() => {
      expect(addToast).toHaveBeenCalledWith('Tag added', 'success');
    });
  });

  it('closes picker on Cancel', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));
    await user.click(screen.getByText('+ Tag'));

    expect(screen.getByText('Select a tag...')).toBeInTheDocument();
    await user.click(screen.getByText('Cancel'));
    expect(screen.queryByText('Select a tag...')).not.toBeInTheDocument();
  });
});
