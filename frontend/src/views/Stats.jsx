import { useState, useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { getStats } from '../api.js';

export function Stats() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(function () {
    loadStats();
  }, []);

  async function loadStats() {
    setLoading(true);
    setError(null);
    try {
      const data = await getStats();
      setStats(data);
    } catch (e) {
      setError(e.message || 'Could not load reading statistics. Try again later.');
    }
    setLoading(false);
  }

  function formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
  }

  if (loading) {
    return (
      <>
        <Header />
        <main class="main-content">
          <LoadingSpinner />
        </main>
      </>
    );
  }

  if (error) {
    return (
      <>
        <Header />
        <main class="main-content">
          <EmptyState title="Failed to load stats">
            {error}
            <br />
            <button class="btn btn-primary mt-4" onClick={loadStats}>
              Retry
            </button>
          </EmptyState>
        </main>
      </>
    );
  }

  if (!stats) return null;

  return (
    <>
      <Header />
      <main class="main-content">
        <h1 class="section-title">Reading Statistics</h1>

        {/* Summary cards */}
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-card-number">{formatNumber(stats.total_articles)}</div>
            <div class="stat-card-label">Total articles</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-number">{formatNumber(stats.total_words_read)}</div>
            <div class="stat-card-label">Words read</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-number">{stats.reading_streak_days}</div>
            <div class="stat-card-label">Day streak</div>
          </div>
          <div class="stat-card">
            <div class="stat-card-number">
              {stats.avg_reading_time_minutes}
              <span class="stat-card-unit">min</span>
            </div>
            <div class="stat-card-label">Avg read time</div>
          </div>
        </div>

        {/* Status breakdown */}
        <div class="stats-section">
          <h2 class="stats-section-title">By Status</h2>
          <div class="stats-status-bar">
            {stats.total_articles > 0 ? (
              <>
                {stats.articles_by_status.unread > 0 && (
                  <div
                    class="stats-status-segment stats-status-segment--unread"
                    style={{
                      width: (stats.articles_by_status.unread / stats.total_articles) * 100 + '%',
                    }}
                    title={'Unread: ' + stats.articles_by_status.unread}
                  />
                )}
                {stats.articles_by_status.archived > 0 && (
                  <div
                    class="stats-status-segment stats-status-segment--archived"
                    style={{
                      width: (stats.articles_by_status.archived / stats.total_articles) * 100 + '%',
                    }}
                    title={'Archived: ' + stats.articles_by_status.archived}
                  />
                )}
              </>
            ) : (
              <div
                class="stats-status-segment stats-status-segment--empty"
                style={{ width: '100%' }}
              />
            )}
          </div>
          <div class="stats-status-legend">
            <span class="stats-legend-item">
              <span class="stats-legend-dot stats-legend-dot--unread"></span>
              Unread: {stats.articles_by_status.unread}
            </span>
            <span class="stats-legend-item">
              <span class="stats-legend-dot stats-legend-dot--archived"></span>
              Archived: {stats.articles_by_status.archived}
            </span>
          </div>
        </div>

        {/* This week / month */}
        <div class="stats-section">
          <h2 class="stats-section-title">Activity</h2>
          <div class="stats-activity-grid">
            <div class="stats-activity-card">
              <div class="stats-activity-period">This Week</div>
              <div class="stats-activity-row">
                <span class="stats-activity-label">Saved</span>
                <span class="stats-activity-value">{stats.articles_this_week}</span>
              </div>
              <div class="stats-activity-row">
                <span class="stats-activity-label">Completed</span>
                <span class="stats-activity-value">{stats.archived_this_week}</span>
              </div>
            </div>
            <div class="stats-activity-card">
              <div class="stats-activity-period">This Month</div>
              <div class="stats-activity-row">
                <span class="stats-activity-label">Saved</span>
                <span class="stats-activity-value">{stats.articles_this_month}</span>
              </div>
              <div class="stats-activity-row">
                <span class="stats-activity-label">Completed</span>
                <span class="stats-activity-value">{stats.archived_this_month}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Top domains */}
        {stats.top_domains.length > 0 && (
          <div class="stats-section">
            <h2 class="stats-section-title">Top Domains</h2>
            <div class="stats-domains-list">
              {stats.top_domains.map(function (d) {
                const maxCount = stats.top_domains[0].count;
                const pct = maxCount > 0 ? (d.count / maxCount) * 100 : 0;
                return (
                  <div class="stats-domain-row" key={d.domain}>
                    <div class="stats-domain-info">
                      <span class="stats-domain-name">{d.domain}</span>
                      <span class="stats-domain-count">{d.count}</span>
                    </div>
                    <div class="stats-domain-bar-bg">
                      <div class="stats-domain-bar-fill" style={{ width: pct + '%' }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Monthly trend */}
        {stats.articles_by_month.length > 0 && (
          <div class="stats-section">
            <h2 class="stats-section-title">Monthly Trend</h2>
            <div class="stats-table-wrap">
              <table class="stats-table">
                <thead>
                  <tr>
                    <th>Month</th>
                    <th>Saved</th>
                    <th>Archived</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.articles_by_month.map(function (m) {
                    return (
                      <tr key={m.month}>
                        <td>{m.month}</td>
                        <td>{m.saved}</td>
                        <td>{m.archived}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </>
  );
}
