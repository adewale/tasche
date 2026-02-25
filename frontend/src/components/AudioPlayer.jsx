import { useRef, useState, useEffect } from 'preact/hooks';
import { signal } from '@preact/signals';
import { addToast } from '../state.js';
import { formatTime } from '../utils.js';
import { IconPlay, IconPause, IconSkipBack, IconSkipForward, IconX } from './Icons.jsx';

export const audioState = signal({
  articleId: null,
  articleTitle: '',
  isPlaying: false,
  visible: false,
});

const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];

let audioEl = null;
export function getAudio() {
  if (!audioEl) {
    audioEl = new Audio();
  }
  return audioEl;
}

export function playAudio(articleId, title) {
  const audio = getAudio();
  audioState.value = {
    articleId,
    articleTitle: title || 'Untitled',
    isPlaying: true,
    visible: true,
  };
  audio.src = '/api/articles/' + articleId + '/audio';
  audio.play().catch(function (e) {
    addToast('Could not play audio: ' + e.message, 'error');
  });
  document.body.classList.add('has-audio-player');
}

export function AudioPlayer() {
  const state = audioState.value;
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speedIndex, setSpeedIndex] = useState(1);
  const progressRef = useRef(null);

  useEffect(() => {
    if (!state.visible) return;

    const audio = getAudio();

    function onTimeUpdate() {
      setCurrentTime(audio.currentTime);
      setDuration(audio.duration || 0);
    }

    function onPlay() {
      setIsPlaying(true);
    }
    function onPause() {
      setIsPlaying(false);
    }
    function onEnded() {
      setIsPlaying(false);
    }
    function onError() {
      addToast('Audio playback error', 'error');
    }

    audio.addEventListener('timeupdate', onTimeUpdate);
    audio.addEventListener('play', onPlay);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('ended', onEnded);
    audio.addEventListener('error', onError);

    setIsPlaying(!audio.paused);
    setCurrentTime(audio.currentTime);
    setDuration(audio.duration || 0);

    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: state.articleTitle || 'Untitled',
        artist: 'Tasche',
      });

      navigator.mediaSession.setActionHandler('play', function () {
        audio.play().catch(function () {});
      });
      navigator.mediaSession.setActionHandler('pause', function () {
        audio.pause();
      });
      navigator.mediaSession.setActionHandler('seekbackward', function () {
        audio.currentTime = Math.max(0, audio.currentTime - 15);
      });
      navigator.mediaSession.setActionHandler('seekforward', function () {
        audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 15);
      });
    }

    return function () {
      audio.removeEventListener('timeupdate', onTimeUpdate);
      audio.removeEventListener('play', onPlay);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('error', onError);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.visible, state.articleId]);

  if (!state.visible) return null;

  function toggle() {
    const audio = getAudio();
    if (!audio.src) return;
    if (audio.paused) {
      audio.play().catch(function () {});
    } else {
      audio.pause();
    }
  }

  function skip(seconds) {
    const audio = getAudio();
    if (!audio.src) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + seconds));
  }

  function cycleSpeed() {
    const newIndex = (speedIndex + 1) % SPEEDS.length;
    setSpeedIndex(newIndex);
    getAudio().playbackRate = SPEEDS[newIndex];
  }

  function stop() {
    const audio = getAudio();
    audio.pause();
    audio.src = '';
    audio.load();
    audioState.value = {
      articleId: null,
      articleTitle: '',
      isPlaying: false,
      visible: false,
    };
    document.body.classList.remove('has-audio-player');
  }

  function handleSeek(e) {
    const audio = getAudio();
    if (!audio.duration) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    audio.currentTime = pct * audio.duration;
  }

  const progressPct = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div class="audio-player-bar visible">
      <div class="audio-player-inner">
        <div class="audio-player-info">
          <div class="audio-player-title">{state.articleTitle}</div>
          <div class="audio-player-time">
            {formatTime(currentTime)} / {formatTime(duration)}
          </div>
        </div>
        <div class="audio-player-controls">
          <button
            title="Back 15s"
            onClick={function () {
              skip(-15);
            }}
          >
            <IconSkipBack />
          </button>
          <button class="play-btn" title={isPlaying ? 'Pause' : 'Play'} onClick={toggle}>
            {isPlaying ? <IconPause size={18} /> : <IconPlay size={18} />}
          </button>
          <button
            title="Forward 15s"
            onClick={function () {
              skip(15);
            }}
          >
            <IconSkipForward />
          </button>
          <button class="audio-speed-btn" title="Playback speed" onClick={cycleSpeed}>
            {SPEEDS[speedIndex]}x
          </button>
          <button class="audio-close-btn" title="Close" onClick={stop}>
            <IconX />
          </button>
        </div>
      </div>
      {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions */}
      <div class="audio-progress" ref={progressRef} onClick={handleSeek}>
        <div class="audio-progress-bar" style={{ width: progressPct + '%' }} />
      </div>
    </div>
  );
}
