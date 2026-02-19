import { user, isOffline, syncStatus } from '../state.js';
import { performLogout } from '../api.js';
import { IconSearch, IconTag, IconSettings } from './Icons.jsx';

export function Header() {
  const u = user.value;
  const offline = isOffline.value;
  const syncing = syncStatus.value;

  async function handleLogout() {
    await performLogout();
  }

  return (
    <>
      <header class="header">
        <div class="header-inner">
          <a href="#/" class="header-logo">
            Tasche
            {offline && (
              <span
                class="offline-badge offline-badge--offline"
                title="Offline"
                role="status"
                aria-label="Offline"
              ></span>
            )}
            {!offline && syncing === 'syncing' && (
              <span
                class="offline-badge offline-badge--syncing"
                title="Syncing..."
                role="status"
                aria-label="Syncing"
              ></span>
            )}
          </a>
          <div class="header-actions">
            {syncing === 'syncing' && (
              <span class="sync-status">Syncing...</span>
            )}
            <a href="#/search" class="btn btn-icon" title="Search">
              <IconSearch />
            </a>
            <a href="#/tags" class="btn btn-icon" title="Tags">
              <IconTag />
            </a>
            <a href="#/settings" class="btn btn-icon" title="Settings">
              <IconSettings />
            </a>
            {u && u.avatar_url && (
              <img class="user-avatar" src={u.avatar_url} alt="Avatar" />
            )}
            <button class="btn btn-sm btn-secondary" onClick={handleLogout}>
              Logout
            </button>
          </div>
        </div>
      </header>
      <div class={'offline-bar' + (offline ? ' visible' : '')}>
        You are offline. Some features may be unavailable.
      </div>
    </>
  );
}
