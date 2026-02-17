export function Pagination({ hasMore, loading, onLoadMore }) {
  if (!hasMore || loading) return null;

  return (
    <div class="load-more">
      <button class="btn btn-secondary" onClick={onLoadMore}>
        Load more
      </button>
    </div>
  );
}
