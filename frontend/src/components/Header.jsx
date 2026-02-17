import { user, isOffline, syncStatus } from '../state.js';
import { logout as apiLogout } from '../api.js';

export function Header() {
  const u = user.value;
  const offline = isOffline.value;
  const syncing = syncStatus.value;

  async function handleLogout() {
    try {
      await apiLogout();
    } catch (e) {
      // ignore
    }
    user.value = null;
    window.location.hash = '#/login';
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
              {'\uD83D\uDD0D'}
            </a>
            <a href="#/tags" class="btn btn-icon" title="Tags">
              {'\uD83C\uDFF7\uFE0F'}
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
