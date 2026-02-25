import { toasts, removeToast } from '../state.js';

export function Toast() {
  const items = toasts.value;
  if (items.length === 0) return null;

  return (
    <div class="toast-container">
      {items.map((t) => (
        // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
        <div key={t.id} class={'toast ' + t.type} onClick={() => removeToast(t.id)}>
          {t.type === 'success' ? '\u2713 ' : t.type === 'error' ? '\u2717 ' : '\u2022 '}
          {t.message}
        </div>
      ))}
    </div>
  );
}
