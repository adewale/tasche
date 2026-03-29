/**
 * Shared audio status derivation used by AudioPlayer and Reader.
 *
 * @param {Object} article - Article object with audio_status field
 * @param {Object} [options] - Additional options
 * @param {boolean} [options.audioRequested] - Whether audio was just requested in this session
 * @returns {{ hasAudio: boolean, audioPending: boolean, audioStuck: boolean, audioFailed: boolean, canRequestAudio: boolean }}
 */
export function getAudioStatusFlags(article, options) {
  const audioRequested = options && options.audioRequested;
  const audioStatus = article ? article.audio_status : null;
  const hasAudio = audioStatus === 'ready';
  const audioPending = !!audioRequested || audioStatus === 'pending';
  const audioStuck = !audioRequested && audioStatus === 'generating';
  const audioFailed = audioStatus === 'failed';
  const canRequestAudio = !hasAudio && !audioPending && !audioStuck && !audioFailed;

  return { hasAudio, audioPending, audioStuck, audioFailed, canRequestAudio };
}
