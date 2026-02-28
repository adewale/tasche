import { render, screen, waitFor } from '@testing-library/preact';
import { Login } from '../../../src/views/Login.jsx';

vi.mock('../../../src/api.js', () => ({
  getHealthConfig: vi.fn(() => Promise.resolve({ status: 'ok', checks: [] })),
}));

import { getHealthConfig } from '../../../src/api.js';

describe('Login', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state initially', () => {
    getHealthConfig.mockReturnValue(new Promise(() => {}));
    render(<Login />);
    expect(screen.getByText('Tasche')).toBeInTheDocument();
    expect(screen.getByText('Checking configuration...')).toBeInTheDocument();
  });

  it('shows sign-in button when health returns ok', async () => {
    getHealthConfig.mockResolvedValue({ status: 'ok', checks: [] });
    render(<Login />);
    await waitFor(() => {
      expect(screen.getByText('Sign in with GitHub')).toBeInTheDocument();
    });
  });

  it('shows sign-in button when health returns degraded', async () => {
    getHealthConfig.mockResolvedValue({
      status: 'degraded',
      checks: [{ name: 'CF_ACCOUNT_ID', required: false, status: 'missing', description: 'Cloudflare account ID' }],
    });
    render(<Login />);
    await waitFor(() => {
      expect(screen.getByText('Sign in with GitHub')).toBeInTheDocument();
    });
  });

  it('shows setup checklist when health returns error', async () => {
    getHealthConfig.mockResolvedValue({
      status: 'error',
      checks: [
        { name: 'DB', required: true, status: 'ok', description: 'D1 database' },
        { name: 'GITHUB_CLIENT_ID', required: true, status: 'missing', description: 'GitHub OAuth app client ID' },
        { name: 'GITHUB_CLIENT_SECRET', required: true, status: 'missing', description: 'GitHub OAuth app client secret' },
        { name: 'ALLOWED_EMAILS', required: true, status: 'missing', description: 'Comma-separated list of allowed emails' },
        { name: 'CF_ACCOUNT_ID', required: false, status: 'missing', description: 'Cloudflare account ID for Browser Rendering' },
      ],
    });
    render(<Login />);
    await waitFor(() => {
      expect(screen.getByText('Setup Checklist')).toBeInTheDocument();
    });
    expect(screen.getByText('Required')).toBeInTheDocument();
    expect(screen.getByText('Optional')).toBeInTheDocument();
    expect(screen.getByText('GITHUB_CLIENT_ID')).toBeInTheDocument();
    expect(screen.getByText('GITHUB_CLIENT_SECRET')).toBeInTheDocument();
    expect(screen.getByText('ALLOWED_EMAILS')).toBeInTheDocument();
    expect(screen.getByText('CF_ACCOUNT_ID')).toBeInTheDocument();
    expect(screen.queryByText('Sign in with GitHub')).not.toBeInTheDocument();
  });

  it('shows help text for missing required items', async () => {
    getHealthConfig.mockResolvedValue({
      status: 'error',
      checks: [
        { name: 'GITHUB_CLIENT_ID', required: true, status: 'missing', description: 'GitHub OAuth app client ID' },
        { name: 'ALLOWED_EMAILS', required: true, status: 'missing', description: 'Comma-separated list of allowed emails' },
      ],
    });
    render(<Login />);
    await waitFor(() => {
      expect(screen.getByText(/Create a GitHub OAuth App at github\.com\/settings\/developers/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Set this to the email on your GitHub account/)).toBeInTheDocument();
  });

  it('does not show help text for items with ok status', async () => {
    getHealthConfig.mockResolvedValue({
      status: 'error',
      checks: [
        { name: 'DB', required: true, status: 'ok', description: 'D1 database' },
        { name: 'GITHUB_CLIENT_ID', required: true, status: 'missing', description: 'GitHub OAuth app client ID' },
      ],
    });
    render(<Login />);
    await waitFor(() => {
      expect(screen.getByText('DB')).toBeInTheDocument();
    });
    var dbItem = screen.getByText('DB').closest('.setup-item');
    expect(dbItem).toHaveClass('setup-item--ok');
  });

  it('shows + indicator for ok items and - for missing', async () => {
    getHealthConfig.mockResolvedValue({
      status: 'error',
      checks: [
        { name: 'DB', required: true, status: 'ok', description: 'D1 database' },
        { name: 'GITHUB_CLIENT_ID', required: true, status: 'missing', description: 'GitHub OAuth app client ID' },
      ],
    });
    render(<Login />);
    await waitFor(() => {
      expect(screen.getByText('DB')).toBeInTheDocument();
    });
    var indicators = screen.getAllByText('+');
    expect(indicators.length).toBeGreaterThanOrEqual(1);
    var missingIndicators = screen.getAllByText('-');
    expect(missingIndicators.length).toBeGreaterThanOrEqual(1);
  });
});
