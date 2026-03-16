import { useState, useEffect, useRef } from 'preact/hooks';

var THRESHOLD = 10;

export function useScrollDirection() {
  var lastY = useRef(window.scrollY);
  var [hidden, setHidden] = useState(false);

  useEffect(function () {
    function onScroll() {
      var y = window.scrollY;
      var diff = y - lastY.current;
      if (diff > THRESHOLD) {
        setHidden(true);
      } else if (diff < -THRESHOLD) {
        setHidden(false);
      }
      lastY.current = y;
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    return function () {
      window.removeEventListener('scroll', onScroll);
    };
  }, []);

  return hidden;
}
