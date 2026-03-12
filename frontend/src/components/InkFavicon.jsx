import { useState, useEffect, useRef } from 'preact/hooks';

/**
 * Data-ink favicon: loads a domain's favicon, renders it to a tiny canvas,
 * and applies Floyd-Steinberg dithering to produce a 1-bit monochrome
 * ink-stamp effect. Falls back to a serif initial letter.
 */
export function InkFavicon({ domain, size = 16 }) {
  const canvasSize = size * 2; // render at 2x for sharpness
  const [src, setSrc] = useState(null);
  const [failed, setFailed] = useState(false);
  const canvasRef = useRef(null);

  useEffect(
    function () {
      if (!domain) {
        setFailed(true);
        return;
      }

      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = function () {
        try {
          const canvas = canvasRef.current;
          if (!canvas) return;
          canvas.width = canvasSize;
          canvas.height = canvasSize;
          const ctx = canvas.getContext('2d');

          // Draw favicon to canvas
          ctx.drawImage(img, 0, 0, canvasSize, canvasSize);
          const imageData = ctx.getImageData(0, 0, canvasSize, canvasSize);
          const data = imageData.data;

          // Convert to greyscale
          for (let i = 0; i < data.length; i += 4) {
            const grey = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
            data[i] = grey;
            data[i + 1] = grey;
            data[i + 2] = grey;
          }

          // Floyd-Steinberg dithering to 1-bit
          const w = canvasSize;
          const h = canvasSize;
          for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
              const idx = (y * w + x) * 4;
              const old = data[idx];
              const val = old < 128 ? 0 : 255;
              const err = old - val;
              data[idx] = val;
              data[idx + 1] = val;
              data[idx + 2] = val;

              // Distribute error to neighbours
              if (x + 1 < w) {
                data[idx + 4] += (err * 7) / 16;
              }
              if (y + 1 < h) {
                if (x > 0) data[idx + w * 4 - 4] += (err * 3) / 16;
                data[idx + w * 4] += (err * 5) / 16;
                if (x + 1 < w) data[idx + w * 4 + 4] += (err * 1) / 16;
              }
            }
          }

          ctx.putImageData(imageData, 0, 0);
          setSrc(canvas.toDataURL('image/png'));
        } catch (_e) {
          setFailed(true);
        }
      };
      img.onerror = function () {
        setFailed(true);
      };
      img.src = 'https://www.google.com/s2/favicons?domain=' + domain + '&sz=32';
    },
    [domain, canvasSize],
  );

  if (failed || !domain) {
    // Fallback: serif initial letter
    const letter = domain ? domain.charAt(0).toUpperCase() : '?';
    return (
      <span class="ink-favicon ink-favicon--letter" aria-hidden="true">
        {letter}
      </span>
    );
  }

  return (
    <>
      <canvas ref={canvasRef} style="display:none" />
      {src ? (
        <img class="ink-favicon" src={src} alt="" width={size} height={size} aria-hidden="true" />
      ) : (
        <span class="ink-favicon ink-favicon--letter" aria-hidden="true">
          {domain.charAt(0).toUpperCase()}
        </span>
      )}
    </>
  );
}
