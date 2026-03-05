import { getHealthConfig, request, getAudioUrl, getArticleContent } from '../../src/api.js';

// Mock state module to prevent import errors
vi.mock('../../src/state.js', () => ({
  user: { value: null },
}));

describe('getHealthConfig', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('returns data on first success without retrying', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: 'ok', checks: [] }),
    });

    var result = await getHealthConfig();

    expect(result).toEqual({ status: 'ok', checks: [] });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('retries on network failure and succeeds', async () => {
    fetch.mockRejectedValueOnce(new Error('Network error')).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: 'ok', checks: [] }),
    });

    var promise = getHealthConfig();

    // Advance past the first retry delay (1500ms * 1)
    await vi.advanceTimersByTimeAsync(1500);

    var result = await promise;
    expect(result).toEqual({ status: 'ok', checks: [] });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('returns unreachable after all retries exhausted', async () => {
    fetch
      .mockRejectedValueOnce(new Error('fail 1'))
      .mockRejectedValueOnce(new Error('fail 2'))
      .mockRejectedValueOnce(new Error('fail 3'));

    var promise = getHealthConfig();

    // Advance past retry delay 1 (1500ms)
    await vi.advanceTimersByTimeAsync(1500);
    // Advance past retry delay 2 (3000ms)
    await vi.advanceTimersByTimeAsync(3000);

    var result = await promise;
    expect(result).toEqual({ status: 'unreachable', checks: [] });
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('retries on non-ok response', async () => {
    fetch.mockResolvedValueOnce({ ok: false, status: 500 }).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: 'ok', checks: [] }),
    });

    var promise = getHealthConfig();

    await vi.advanceTimersByTimeAsync(1500);

    var result = await promise;
    expect(result).toEqual({ status: 'ok', checks: [] });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe('request', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns JSON data on success', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: { get: (h) => (h === 'content-type' ? 'application/json' : null) },
      json: () => Promise.resolve({ id: '123', title: 'Test' }),
    });

    var result = await request('GET', '/api/articles/123');
    expect(result).toEqual({ id: '123', title: 'Test' });
  });

  it('returns null on 204 No Content', async () => {
    fetch.mockResolvedValueOnce({ ok: true, status: 204 });

    var result = await request('DELETE', '/api/articles/123');
    expect(result).toBeNull();
  });

  it('throws with JSON detail on error response', async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      text: () => Promise.resolve('{"detail":"Article not found"}'),
    });

    await expect(request('GET', '/api/articles/123')).rejects.toThrow('Article not found');
  });

  it('throws with statusText when response is not JSON', async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      text: () => Promise.resolve('<html>Worker threw exception</html>'),
    });

    await expect(request('GET', '/api/articles/123')).rejects.toThrow('Internal Server Error');
  });

  it('logs structured error to console.error on failure', async () => {
    var spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      text: () => Promise.resolve('Worker crashed'),
    });

    await expect(request('GET', '/api/test')).rejects.toThrow();

    expect(spy).toHaveBeenCalledWith(
      '[API] %s %s → %d %s',
      'GET',
      '/api/test',
      503,
      'Service Unavailable',
      'Worker crashed',
    );
    spy.mockRestore();
  });

  it('attaches status code to thrown error', async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve('{"detail":"Duplicate URL"}'),
    });

    try {
      await request('POST', '/api/articles', { url: 'https://example.com' });
    } catch (e) {
      expect(e.status).toBe(409);
      expect(e.message).toBe('Duplicate URL');
    }
  });

  it('sends JSON body for POST requests', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: { get: (h) => (h === 'content-type' ? 'application/json' : null) },
      json: () => Promise.resolve({ id: 'new' }),
    });

    await request('POST', '/api/articles', { url: 'https://example.com' });

    expect(fetch).toHaveBeenCalledWith('/api/articles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: '{"url":"https://example.com"}',
    });
  });
});

describe('getAudioUrl', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    vi.stubGlobal(
      'URL',
      Object.assign({}, globalThis.URL, {
        createObjectURL: vi.fn(() => 'blob:http://localhost/audio-123'),
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns a blob URL on success', async () => {
    var audioBlob = new Blob(['fake-audio'], { type: 'audio/wav' });
    fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      blob: () => Promise.resolve(audioBlob),
    });

    var url = await getAudioUrl('abc123');
    expect(url).toBe('blob:http://localhost/audio-123');
    expect(fetch).toHaveBeenCalledWith('/api/articles/abc123/audio', {
      credentials: 'include',
    });
  });

  it('throws with status and detail on JSON error', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve('{"detail":"Audio is still being generated"}'),
    });

    await expect(getAudioUrl('abc123')).rejects.toThrow('409: Audio is still being generated');
    console.error.mockRestore();
  });

  it('throws with status and statusText on non-JSON error', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      text: () => Promise.resolve('<html>Worker threw exception</html>'),
    });

    await expect(getAudioUrl('abc123')).rejects.toThrow('500: Internal Server Error');
    console.error.mockRestore();
  });

  it('logs structured error to console on failure', async () => {
    var spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      text: () => Promise.resolve('crash'),
    });

    await expect(getAudioUrl('x')).rejects.toThrow();

    expect(spy).toHaveBeenCalledWith(
      '[API] GET %s → %d %s',
      '/api/articles/x/audio',
      503,
      'Service Unavailable',
      'crash',
    );
    spy.mockRestore();
  });
});

describe('fetchText (via getArticleContent)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns text content on success', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: () => Promise.resolve('<h1>Article</h1>'),
    });

    var result = await getArticleContent('abc');
    expect(result).toBe('<h1>Article</h1>');
  });

  it('returns null and logs on error response', async () => {
    var spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: 'Not Found',
    });

    var result = await getArticleContent('missing');
    expect(result).toBeNull();
    expect(spy).toHaveBeenCalledWith(
      '[API] GET %s → %d %s',
      '/api/articles/missing/content',
      404,
      'Not Found',
    );
    spy.mockRestore();
  });

  it('returns null and logs on network failure', async () => {
    var spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetch.mockRejectedValueOnce(new Error('Network down'));

    var result = await getArticleContent('abc');
    expect(result).toBeNull();
    expect(spy).toHaveBeenCalledWith(
      '[API] GET %s failed:',
      '/api/articles/abc/content',
      'Network down',
    );
    spy.mockRestore();
  });
});
