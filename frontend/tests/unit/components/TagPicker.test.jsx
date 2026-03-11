import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { TagPicker } from '../../../src/components/TagPicker.jsx';

vi.mock('../../../src/api.js', () => ({
  listTags: vi.fn(() => Promise.resolve([])),
  createTag: vi.fn(() => Promise.resolve({ id: 'tag-new', name: 'newtag' })),
  getArticleTags: vi.fn(() => Promise.resolve([{ id: 'tag-1', name: 'JavaScript' }])),
  addArticleTag: vi.fn(() => Promise.resolve()),
  removeArticleTag: vi.fn(() => Promise.resolve()),
}));

vi.mock('../../../src/state.js', () => ({
  tags: {
    value: [
      { id: 'tag-2', name: 'Python' },
      { id: 'tag-3', name: 'Rust' },
    ],
  },
  addToast: vi.fn(),
}));

import { addArticleTag, removeArticleTag, createTag } from '../../../src/api.js';
import { addToast, tags as tagsSignal } from '../../../src/state.js';

describe('TagPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    tagsSignal.value = [
      { id: 'tag-2', name: 'Python' },
      { id: 'tag-3', name: 'Rust' },
    ];
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

  it('opens autocomplete picker on + Tag click', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    expect(screen.getByPlaceholderText('Type to filter or create...')).toBeInTheDocument();
  });

  it('shows available tags (excluding already-applied) in dropdown', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    // JavaScript is already applied, so only Python and Rust show
    expect(screen.getByText('Python')).toBeInTheDocument();
    expect(screen.getByText('Rust')).toBeInTheDocument();
  });

  it('filters suggestions as user types', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    await user.type(screen.getByPlaceholderText('Type to filter or create...'), 'py');

    // Only Python matches
    expect(
      screen.getByText(
        (_, el) => el.closest('.tag-picker-option') && el.textContent.includes('Python'),
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('Rust')).not.toBeInTheDocument();
  });

  it('adds tag when clicking a suggestion', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));

    // mouseDown on the Python option
    const option = screen.getByText('Python').closest('.tag-picker-option');
    await user.pointer({ keys: '[MouseLeft>]', target: option });

    await waitFor(() => {
      expect(addArticleTag).toHaveBeenCalledWith('art-1', 'tag-2');
      expect(addToast).toHaveBeenCalledWith('Tag added', 'success');
    });
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

  it('shows toast on successful tag removal', async () => {
    const user = userEvent.setup();
    removeArticleTag.mockResolvedValueOnce();

    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('JavaScript'));

    await user.click(screen.getByText('\u00D7'));

    await waitFor(() => {
      expect(removeArticleTag).toHaveBeenCalledWith('art-1', 'tag-1');
      expect(addToast).toHaveBeenCalledWith('Tag removed', 'success');
    });
  });

  it('closes picker on Cancel', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));
    await user.click(screen.getByText('+ Tag'));

    expect(screen.getByPlaceholderText('Type to filter or create...')).toBeInTheDocument();
    await user.click(screen.getByText('Cancel'));
    expect(screen.queryByPlaceholderText('Type to filter or create...')).not.toBeInTheDocument();
  });

  it('closes picker on Escape key', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));
    await user.click(screen.getByText('+ Tag'));

    const input = screen.getByPlaceholderText('Type to filter or create...');
    input.focus();
    await user.keyboard('{Escape}');
    expect(screen.queryByPlaceholderText('Type to filter or create...')).not.toBeInTheDocument();
  });

  it('shows "Create" option when typed text has no exact match', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    await user.type(screen.getByPlaceholderText('Type to filter or create...'), 'golang');

    expect(screen.getByText('+ Create "golang"')).toBeInTheDocument();
  });

  it('does not show "Create" option when typed text matches an existing tag', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    await user.type(screen.getByPlaceholderText('Type to filter or create...'), 'python');

    expect(screen.queryByText(/Create/)).not.toBeInTheDocument();
  });

  it('selects highlighted suggestion with Enter key', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    const input = screen.getByPlaceholderText('Type to filter or create...');
    // First option (Python) is highlighted by default
    await user.type(input, '{Enter}');

    await waitFor(() => {
      expect(addArticleTag).toHaveBeenCalledWith('art-1', 'tag-2');
    });
  });

  it('navigates suggestions with arrow keys', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    const input = screen.getByPlaceholderText('Type to filter or create...');
    // Move down to second option (Rust)
    await user.type(input, '{ArrowDown}{Enter}');

    await waitFor(() => {
      expect(addArticleTag).toHaveBeenCalledWith('art-1', 'tag-3');
    });
  });

  it('has correct input attributes for mobile', async () => {
    const user = userEvent.setup();
    render(<TagPicker articleId="art-1" />);
    await waitFor(() => screen.getByText('+ Tag'));

    await user.click(screen.getByText('+ Tag'));
    const input = screen.getByPlaceholderText('Type to filter or create...');
    expect(input).toHaveAttribute('autoCapitalize', 'off');
    expect(input).toHaveAttribute('autoCorrect', 'off');
  });
});
