import { useState, useEffect, useRef } from 'preact/hooks';

/**
 * Ink-wash thumbnail: loads an article thumbnail, renders it through a
 * sumi-e pipeline (desaturate → posterize to 4 tonal levels → slight blur).
 * On hover, the full-colour original fades in.
 */
export function InkWashThumbnail({ src, alt }) {
  const [inkSrc, setInkSrc] = useState(null);
  const [error, setError] = useState(false);
  const canvasRef = useRef(null);

  useEffect(
    function () {
      if (!src) return;

      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = function () {
        try {
          const canvas = canvasRef.current;
          if (!canvas) return;

          // Render at the image's natural size, capped at 176px
          const w = Math.min(img.naturalWidth, 176);
          const h = Math.round((w / img.naturalWidth) * img.naturalHeight);
          canvas.width = w;
          canvas.height = h;
          const ctx = canvas.getContext('2d');

          ctx.drawImage(img, 0, 0, w, h);
          const imageData = ctx.getImageData(0, 0, w, h);
          const data = imageData.data;

          // Desaturate + posterize to 4 tonal levels
          const levels = [0, 85, 170, 255];
          for (let i = 0; i < data.length; i += 4) {
            const grey = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];

            // Quantize to nearest of 4 levels
            let closest = levels[0];
            let minDist = Math.abs(grey - closest);
            for (let l = 1; l < levels.length; l++) {
              const dist = Math.abs(grey - levels[l]);
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
          const tempCanvas = document.createElement('canvas');
          tempCanvas.width = w;
          tempCanvas.height = h;
          const tempCtx = tempCanvas.getContext('2d');
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
