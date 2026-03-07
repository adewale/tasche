import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { Header } from '../../../src/components/Header.jsx';

vi.mock('../../../src/state.js', () => {
  const { signal } = require('@preact/signals');
  return {
    isOffline: signal(false),
    syncStatus: signal('idle'),
    theme: signal('system'),
    applyTheme: vi.fn(),
    showShortcuts: signal(false),
  };
});

const mockUpdatePref = vi.fn();
vi.mock('../../../src/readerPrefs.js', () => {
  const { signal } = require('@preact/signals');
  return {
    readerPrefs: signal({ theme: 'auto' }),
    updatePref: (...args) => mockUpdatePref(...args),
  };
});

describe('Header', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows global theme toggle when readerMode is not set', async () => {
    const user = userEvent.setup();
    render(<Header />);

    await user.click(screen.getByTitle('Menu'));
    expect(screen.getByText('Dark mode')).toBeInTheDocument();
    expect(screen.queryByText('Reader theme')).not.toBeInTheDocument();
  });

  it('shows reader theme options when readerMode is set', async () => {
    const user = userEvent.setup();
    render(<Header readerMode />);

    await user.click(screen.getByTitle('Menu'));
    expect(screen.getByText('Reader theme')).toBeInTheDocument();
    expect(screen.getByText('Auto')).toBeInTheDocument();
    expect(screen.getByText('Light')).toBeInTheDocument();
    expect(screen.getByText('Sepia')).toBeInTheDocument();
    expect(screen.getByText('Dark')).toBeInTheDocument();
    expect(screen.queryByText('Dark mode')).not.toBeInTheDocument();
  });

  it('calls updatePref when a reader theme option is clicked', async () => {
    const user = userEvent.setup();
    render(<Header readerMode />);

    await user.click(screen.getByTitle('Menu'));
    await user.click(screen.getByText('Sepia'));
    expect(mockUpdatePref).toHaveBeenCalledWith('theme', 'sepia');
  });

  it('closes menu after selecting a reader theme', async () => {
    const user = userEvent.setup();
    render(<Header readerMode />);

    await user.click(screen.getByTitle('Menu'));
    expect(screen.getByText('Sepia')).toBeInTheDocument();

    await user.click(screen.getByText('Sepia'));
    expect(screen.queryByText('Reader theme')).not.toBeInTheDocument();
  });

  it('highlights the active reader theme', async () => {
    const user = userEvent.setup();
    render(<Header readerMode />);

    await user.click(screen.getByTitle('Menu'));
    const autoBtn = screen.getByText('Auto');
    expect(autoBtn.className).toContain('active');

    const sepiaBtn = screen.getByText('Sepia');
    expect(sepiaBtn.className).not.toContain('active');
  });
});
