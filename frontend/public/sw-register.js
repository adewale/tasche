// Service worker registration — external file so the strict CSP
// (script-src 'self', no 'unsafe-inline') applies to index.html.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    var reloading = false;
    navigator.serviceWorker.register('/sw.js').catch(function (err) {
      console.warn('Service Worker registration failed:', err);
    });
    navigator.serviceWorker.addEventListener('controllerchange', function () {
      if (!reloading) {
        reloading = true;
        window.location.reload();
      }
    });
  });
}
