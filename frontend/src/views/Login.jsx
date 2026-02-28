import { useState, useEffect } from 'preact/hooks';
import { getHealthConfig } from '../api.js';

var HELP_TEXT = {
  GITHUB_CLIENT_ID: 'Create a GitHub OAuth App at github.com/settings/developers — set the callback URL to this site\'s URL + /api/auth/callback',
  GITHUB_CLIENT_SECRET: 'From your GitHub OAuth App at github.com/settings/developers',
  ALLOWED_EMAILS: 'Set this to the email on your GitHub account — run: npx wrangler secret put ALLOWED_EMAILS',
  SITE_URL: 'Auto-detected from your URL. Only set this if using a custom domain.',
  READABILITY: 'Optional — improves content extraction. Without it, the built-in parser handles most pages.',
  CF_ACCOUNT_ID: 'Optional — enables screenshots of JS-heavy pages via Browser Rendering',
  CF_API_TOKEN: 'Optional — enables screenshots of JS-heavy pages via Browser Rendering',
};

function SetupChecklist({ checks }) {
  var required = checks.filter(function (c) {
    return c.required;
  });
  var optional = checks.filter(function (c) {
    return !c.required;
  });

  return (
    <div class="setup-checklist">
      <h2 class="setup-heading">Setup Checklist</h2>
      <p class="setup-subtext">Configure these items to get started.</p>

      {required.length > 0 && (
        <div class="setup-group">
          <h3 class="setup-group-label">Required</h3>
          <ul class="setup-items">
            {required.map(function (check) {
              return (
                <li
                  key={check.name}
                  class={
                    'setup-item' + (check.status === 'ok' ? ' setup-item--ok' : ' setup-item--missing')
                  }
                >
                  <span class="setup-item-indicator">{check.status === 'ok' ? '+' : '-'}</span>
                  <div class="setup-item-content">
                    <span class="setup-item-name">{check.name}</span>
                    <span class="setup-item-desc">{check.description}</span>
                    {check.status === 'missing' && HELP_TEXT[check.name] && (
                      <span class="setup-item-help">{HELP_TEXT[check.name]}</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {optional.length > 0 && (
        <div class="setup-group">
          <h3 class="setup-group-label">Optional</h3>
          <ul class="setup-items">
            {optional.map(function (check) {
              return (
                <li
                  key={check.name}
                  class={
                    'setup-item' + (check.status === 'ok' ? ' setup-item--ok' : ' setup-item--missing')
                  }
                >
                  <span class="setup-item-indicator">{check.status === 'ok' ? '+' : '-'}</span>
                  <div class="setup-item-content">
                    <span class="setup-item-name">{check.name}</span>
                    <span class="setup-item-desc">{check.description}</span>
                    {check.status === 'missing' && HELP_TEXT[check.name] && (
                      <span class="setup-item-help">{HELP_TEXT[check.name]}</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

export function Login() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(function () {
    getHealthConfig().then(function (data) {
      setConfig(data);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div class="login-page">
        <h1>Tasche</h1>
        <p>Checking configuration...</p>
      </div>
    );
  }

  var showSetup = config && config.status === 'error';

  return (
    <div class="login-page">
      <h1>Tasche</h1>
      {showSetup ? (
        <SetupChecklist checks={config.checks} />
      ) : (
        <>
          <p>Sign in to continue</p>
          <a href="/api/auth/login" class="btn btn-primary login-btn">
            Sign in with GitHub
          </a>
        </>
      )}
    </div>
  );
}
