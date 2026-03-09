import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { Header } from '../../../src/components/Header.jsx';

// Real modules — no mocks for state.js or readerPrefs.js
// Both use @preact/signals which work natively in jsdom.
// state.js: isOffline, syncStatus, theme, showShortcuts are real signals
// readerPrefs.js: readerPrefs signal + updatePref + getReaderStyle are real

import { theme, showShortcuts } from '../../../src/state.js';
import { readerPrefs } from '../../../src/readerPrefs.js';

describe('Header', () => {
  beforeEach(() => {
    // Reset signal state between tests
    theme.value = 'system';
    showShortcuts.value = false;
    readerPrefs.value = { ...readerPrefs.value, theme: 'auto' };
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

  it('updates reader prefs when a reader theme option is clicked', async () => {
    const user = userEvent.setup();
    render(<Header readerMode />);

    await user.click(screen.getByTitle('Menu'));
    await user.click(screen.getByText('Sepia'));
    expect(readerPrefs.value.theme).toBe('sepia');
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
