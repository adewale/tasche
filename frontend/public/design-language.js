// Design-language page interactions — external file so the strict CSP
// (script-src 'self', no 'unsafe-inline') applies to this page.
function toggleTheme() {
  var root = document.documentElement;
  var isDark = root.getAttribute('data-theme') === 'dark';
  root.setAttribute('data-theme', isDark ? 'light' : 'dark');

  // Swap swatch hex labels and colour previews
  document.querySelectorAll('.swatch-hex[data-light]').forEach(function(el) {
    el.textContent = isDark ? el.dataset.light : el.dataset.dark;
  });
  document.querySelectorAll('.swatch-color[data-light]').forEach(function(el) {
    el.style.background = isDark ? el.dataset.light : el.dataset.dark;
  });

  // Swap marching ants stroke colour
  document.querySelectorAll('.demo-march rect').forEach(function(el) {
    el.style.stroke = isDark
      ? 'rgba(245,245,247,0.25)'
      : 'rgba(29,29,31,0.2)';
  });
}

// Draw a dithered favicon demo
(function() {
  var c = document.getElementById('favicon-dithered-demo');
  if (!c) return;
  var ctx = c.getContext('2d');
  // Simple 16x16 pattern simulating Floyd-Steinberg dithered output
  var pattern = [
    1,1,0,1,0,0,0,0,0,0,0,1,0,1,1,1,
    1,0,0,0,0,1,0,1,0,1,0,0,0,0,1,0,
    0,0,1,0,0,0,0,0,0,0,1,0,1,0,0,0,
    0,1,0,0,1,0,1,0,1,0,0,0,0,1,0,1,
    1,0,0,0,0,0,0,1,0,0,1,0,0,0,0,0,
    0,0,1,0,1,0,0,0,0,1,0,0,1,0,1,0,
    0,0,0,0,0,1,0,0,1,0,0,0,0,0,0,1,
    1,0,1,0,0,0,0,1,0,0,1,0,1,0,0,0,
    0,0,0,1,0,0,1,0,0,0,0,1,0,0,1,0,
    0,1,0,0,0,0,0,0,1,0,0,0,0,1,0,0,
    1,0,0,0,1,0,1,0,0,1,0,0,1,0,0,1,
    0,0,1,0,0,0,0,0,0,0,1,0,0,0,0,0,
    0,0,0,1,0,1,0,1,0,0,0,0,1,0,1,0,
    1,0,0,0,0,0,0,0,1,0,1,0,0,0,0,1,
    0,1,0,1,0,0,1,0,0,0,0,1,0,1,0,0,
    0,0,0,0,1,0,0,1,0,1,0,0,0,0,1,0,
  ];
  var id = ctx.createImageData(16, 16);
  for (var i = 0; i < 256; i++) {
    var v = pattern[i] ? 0 : 255;
    id.data[i*4] = v; id.data[i*4+1] = v; id.data[i*4+2] = v; id.data[i*4+3] = 255;
  }
  ctx.putImageData(id, 0, 0);
})();

document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
