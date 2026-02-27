import { useState, useEffect, useRef } from 'preact/hooks';

/**
 * Data-ink favicon: loads a domain's favicon, renders it to a tiny canvas,
 * and applies Floyd-Steinberg dithering to produce a 1-bit monochrome
 * ink-stamp effect. Falls back to a serif initial letter.
 */
export function InkFavicon({ domain, size = 16 }) {
  var canvasSize = size * 2; // render at 2x for sharpness
  var [src, setSrc] = useState(null);
  var [failed, setFailed] = useState(false);
  var canvasRef = useRef(null);

  useEffect(
    function () {
      if (!domain) {
        setFailed(true);
        return;
      }

      var img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = function () {
        try {
          var canvas = canvasRef.current;
          if (!canvas) return;
          canvas.width = canvasSize;
          canvas.height = canvasSize;
          var ctx = canvas.getContext('2d');

          // Draw favicon to canvas
          ctx.drawImage(img, 0, 0, canvasSize, canvasSize);
          var imageData = ctx.getImageData(0, 0, canvasSize, canvasSize);
          var data = imageData.data;

          // Convert to greyscale
          for (var i = 0; i < data.length; i += 4) {
            var grey = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
            data[i] = grey;
            data[i + 1] = grey;
            data[i + 2] = grey;
          }

          // Floyd-Steinberg dithering to 1-bit
          var w = canvasSize;
          var h = canvasSize;
          for (var y = 0; y < h; y++) {
            for (var x = 0; x < w; x++) {
              var idx = (y * w + x) * 4;
              var old = data[idx];
              var val = old < 128 ? 0 : 255;
              var err = old - val;
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
    var letter = domain ? domain.charAt(0).toUpperCase() : '?';
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
        <img
          class="ink-favicon"
          src={src}
          alt=""
          width={size}
          height={size}
          aria-hidden="true"
        />
      ) : (
        <span class="ink-favicon ink-favicon--letter" aria-hidden="true">
          {domain.charAt(0).toUpperCase()}
        </span>
      )}
    </>
  );
}
