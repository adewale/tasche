import { useState, useEffect, useRef } from 'preact/hooks';

/**
 * Ink-wash thumbnail: loads an article thumbnail, renders it through a
 * sumi-e pipeline (desaturate → posterize to 4 tonal levels → slight blur).
 * On hover, the full-colour original fades in.
 */
export function InkWashThumbnail({ src, alt }) {
  var [inkSrc, setInkSrc] = useState(null);
  var [error, setError] = useState(false);
  var canvasRef = useRef(null);

  useEffect(
    function () {
      if (!src) return;

      var img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = function () {
        try {
          var canvas = canvasRef.current;
          if (!canvas) return;

          // Render at the image's natural size, capped at 176px
          var w = Math.min(img.naturalWidth, 176);
          var h = Math.round((w / img.naturalWidth) * img.naturalHeight);
          canvas.width = w;
          canvas.height = h;
          var ctx = canvas.getContext('2d');

          ctx.drawImage(img, 0, 0, w, h);
          var imageData = ctx.getImageData(0, 0, w, h);
          var data = imageData.data;

          // Desaturate + posterize to 4 tonal levels
          var levels = [0, 85, 170, 255];
          for (var i = 0; i < data.length; i += 4) {
            var grey = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];

            // Quantize to nearest of 4 levels
            var closest = levels[0];
            var minDist = Math.abs(grey - closest);
            for (var l = 1; l < levels.length; l++) {
              var dist = Math.abs(grey - levels[l]);
              if (dist < minDist) {
                minDist = dist;
                closest = levels[l];
              }
            }

            data[i] = closest;
            data[i + 1] = closest;
            data[i + 2] = closest;
          }

          ctx.putImageData(imageData, 0, 0);

          // Apply slight blur (1px) via CSS filter on the canvas is not possible
          // after getImageData, so we re-draw with a blur. Use a second pass:
          // draw the posterized result onto itself with blur.
          var tempCanvas = document.createElement('canvas');
          tempCanvas.width = w;
          tempCanvas.height = h;
          var tempCtx = tempCanvas.getContext('2d');
          tempCtx.filter = 'blur(1px)';
          tempCtx.drawImage(canvas, 0, 0);

          setInkSrc(tempCanvas.toDataURL('image/png'));
        } catch (_e) {
          setError(true);
        }
      };
      img.onerror = function () {
        setError(true);
      };
      img.src = src;
    },
    [src],
  );

  if (error || !src) return null;

  return (
    <div class="ink-wash-thumbnail">
      <canvas ref={canvasRef} style="display:none" />
      {inkSrc && <img class="ink-wash-thumbnail-ink" src={inkSrc} alt={alt || ''} loading="lazy" />}
      <img
        class="ink-wash-thumbnail-color"
        src={src}
        alt={alt || ''}
        loading="lazy"
        onError={function () {
          setError(true);
        }}
      />
    </div>
  );
}
