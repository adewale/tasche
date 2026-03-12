import { useState, useEffect } from 'preact/hooks';
import { getHealthConfig } from '../api.js';
import { IconCheck, IconX } from '../components/Icons.jsx';

function envFlag(env) {
  return env ? ' --env ' + env : '';
}

function getHelpText(name, env) {
  const flag = envFlag(env);
  const texts = {
    DB: 'D1 database binding — check that your wrangler.jsonc has a [[d1_databases]] entry named "DB"',
    CONTENT:
      'R2 bucket binding — check that your wrangler.jsonc has a [[r2_buckets]] entry named "CONTENT"',
    SESSIONS:
      'KV namespace binding — check that your wrangler.jsonc has a [[kv_namespaces]] entry named "SESSIONS"',
    ARTICLE_QUEUE:
      'Queue binding — check that your wrangler.jsonc has a [[queues.producers]] entry named "ARTICLE_QUEUE"',
    AI: 'Workers AI binding — check that your wrangler.jsonc has an [ai] entry named "AI"',
    GITHUB_CLIENT_ID:
      "Create a GitHub OAuth App at github.com/settings/developers — set the callback URL to this site's URL + /api/auth/callback. Then run: npx wrangler secret put GITHUB_CLIENT_ID" +
      flag,
    GITHUB_CLIENT_SECRET:
      'From your GitHub OAuth App — run: npx wrangler secret put GITHUB_CLIENT_SECRET' + flag,
    ALLOWED_EMAILS:
      'Set this to the email on your GitHub account — run: npx wrangler secret put ALLOWED_EMAILS' +
      flag,
    SITE_URL: 'Auto-detected from your URL. Only set this if using a custom domain.',
    READABILITY:
      'Optional — improves content extraction. Without it, the built-in parser handles most pages.',
  };
  return texts[name] || null;
}

function getProgressSummary(status, checks) {
  if (status === 'error') {
    const required = checks.filter(function (c) {
      return c.required;
    });
    const configuredCount = required.filter(function (c) {
      return c.status === 'ok';
    }).length;
    return configuredCount + ' of ' + required.length + ' required items configured.';
  }
  if (status === 'degraded') {
    return 'Some optional items are not configured.';
  }
  return 'All items configured.';
}

function SetupChecklist({ checks, status, environment }) {
  const required = checks.filter(function (c) {
    return c.required;
  });
  const optional = checks.filter(function (c) {
    return !c.required;
  });

  const heading = status === 'error' ? 'Setup Checklist' : 'Configuration';
  const summary = getProgressSummary(status, checks);

  return (
    <div class="setup-checklist">
      <h2 class="setup-heading">{heading}</h2>
      <p class="setup-subtext">{summary}</p>

      {required.length > 0 && (
        <div class="setup-group">
          <h3 class="setup-group-label">Required</h3>
          <ul class="setup-items">
            {required.map(function (check) {
              const indicatorClass =
                check.status === 'ok'
                  ? 'setup-item-indicator setup-item-indicator--ok'
                  : 'setup-item-indicator setup-item-indicator--missing';
              const helpText = getHelpText(check.name, environment);
              return (
                <li
                  key={check.name}
                  class={
                    'setup-item' +
                    (check.status === 'ok' ? ' setup-item--ok' : ' setup-item--missing')
                  }
                >
                  <span class={indicatorClass}>
                    {check.status === 'ok' ? <IconCheck size={14} /> : <IconX size={14} />}
                  </span>
                  <div class="setup-item-content">
                    <span class="setup-item-name">{check.name}</span>
                    <span class="setup-item-desc">{check.description}</span>
                    {check.status === 'missing' && helpText && (
                      <span class="setup-item-help">{helpText}</span>
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
              const indicatorClass =
                check.status === 'ok'
                  ? 'setup-item-indicator setup-item-indicator--ok'
                  : 'setup-item-indicator setup-item-indicator--optional';
              const helpText = getHelpText(check.name, environment);
              return (
                <li
                  key={check.name}
                  class={
                    'setup-item' +
                    (check.status === 'ok' ? ' setup-item--ok' : ' setup-item--missing')
                  }
                >
                  <span class={indicatorClass}>
                    {check.status === 'ok' ? <IconCheck size={14} /> : <IconX size={14} />}
                  </span>
                  <div class="setup-item-content">
                    <span class="setup-item-name">{check.name}</span>
                    <span class="setup-item-desc">{check.description}</span>
                    {check.status === 'missing' && helpText && (
                      <span class="setup-item-help">{helpText}</span>
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

function getHashError() {
  const hash = window.location.hash;
  const qIndex = hash.indexOf('?');
  if (qIndex === -1) return null;
  const params = new URLSearchParams(hash.slice(qIndex));
  return params.get('error');
}

export function Login() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error] = useState(getHashError);

  useEffect(
    function () {
      // Clean the error param from the hash so it doesn't persist on refresh
      if (error) {
        window.location.hash = '#/login';
      }
    },
    [error],
  );

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

  const isError = config && config.status === 'error';
  const isUnreachable = config && config.status === 'unreachable';
  const environment = (config && config.environment) || '';

  function retryHealthCheck() {
    setLoading(true);
    getHealthConfig().then(function (data) {
      setConfig(data);
      setLoading(false);
    });
  }

  return (
    <div class="login-page">
      <h1>Tasche</h1>
      {error === 'not_owner' && (
        <div class="login-error">
          <p>This is a personal Tasche instance and your account isn't on the access list.</p>
          <p class="login-error-hint">
            If you'd like your own,{' '}
            <a href="https://github.com/adewale/tasche" target="_blank" rel="noopener noreferrer">
              Tasche is open source
            </a>{' '}
            — you can deploy your own instance on Cloudflare Workers.
          </p>
        </div>
      )}
      {isUnreachable ? (
        <div class="setup-checklist">
          <h2 class="setup-heading">Connection Issue</h2>
          <p class="setup-subtext">
            Could not reach the server. This usually resolves in a few seconds after a deploy.
          </p>
          <button class="btn btn-primary" onClick={retryHealthCheck}>
            Retry
          </button>
        </div>
      ) : isError ? (
        <SetupChecklist checks={config.checks} status={config.status} environment={environment} />
      ) : (
        <>
          <p>Sign in to continue</p>
          <a href="/api/auth/login" class="btn btn-primary login-btn">
            Sign in with GitHub
          </a>
          {config && config.checks && config.checks.length > 0 && (
            <>
              <hr class="setup-divider" />
              <SetupChecklist
                checks={config.checks}
                status={config.status}
                environment={environment}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}
