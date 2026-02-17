import { Header } from '../components/Header.jsx';
import { user, addToast } from '../state.js';
import { logout as apiLogout } from '../api.js';
import { getBookmarkletCode } from '../utils.js';

export function Settings() {
  const u = user.value;

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
      <Header />
      <main class="main-content">
        <h2 class="section-title">Settings</h2>

        <div class="mt-4">
          <h3 class="section-title">Bookmarklet</h3>
          <p class="text-muted mb-4" style="font-size:0.875rem">
            Drag this link to your bookmarks bar to save articles from any page:
          </p>
          <a
            href={getBookmarkletCode()}
            class="btn btn-secondary"
            onClick={(e) => e.preventDefault()}
          >
            {'\uD83D\uDCCC'} Save to Tasche
          </a>
        </div>

        <div class="mt-8">
          {u && (
            <p style="font-size:0.875rem;margin-bottom:16px;">
              Logged in as: <strong>{u.email || u.username || 'Unknown'}</strong>
            </p>
          )}
          <button class="btn btn-secondary" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </main>
    </>
  );
}
