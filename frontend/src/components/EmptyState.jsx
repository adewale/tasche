export function EmptyState({ icon: Icon, title, children }) {
  return (
    <div class="empty-state">
      {Icon && (
        <div class="empty-state-icon">
          <Icon />
        </div>
      )}
      <div class="empty-state-title">{title}</div>
      {children && <div class="empty-state-text">{children}</div>}
    </div>
  );
}

export function LoadingSpinner() {
  return (
    <div class="loading">
      <div class="spinner"></div>
    </div>
  );
}
