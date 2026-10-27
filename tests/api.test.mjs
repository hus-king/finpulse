import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';
import { createServer } from 'node:http';
import { api, ApiError, setCsrfToken } from '../src/api.ts';

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
afterEach(() => { globalThis.fetch = originalFetch; globalThis.window = originalWindow; setCsrfToken(null); });

test('cancelling while the response body is being read stays an AbortError', async () => {
  const controller = new AbortController();
  globalThis.fetch = async () => ({ status: 200, ok: true, text: async () => {
    controller.abort();
    throw controller.signal.reason;
  } });
  await assert.rejects(api('/api/dashboard/600519', undefined, controller.signal), { name: 'AbortError' });
});

test('an abort immediately after the body resolves cannot become stale data', async () => {
  const controller = new AbortController();
  globalThis.fetch = async () => ({ status: 200, ok: true, text: async () => {
    controller.abort();
    return '{"obsolete":true}';
  } });
  await assert.rejects(api('/api/stocks', undefined, controller.signal), { name: 'AbortError' });
});

test('a real streamed HTTP response cancelled after headers keeps its cancellation', async () => {
  const server = createServer((request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.write('{"stock":');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const controller = new AbortController();
  globalThis.fetch = async (...args) => {
    const response = await originalFetch(...args);
    controller.abort();
    return response;
  };
  try {
    await assert.rejects(api(`http://127.0.0.1:${server.address().port}/api/dashboard/600519`, undefined, controller.signal), { name: 'AbortError' });
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

test('successful HTML, empty, null and malformed responses identify the endpoint without query data', async () => {
  for (const [body, contentType, code] of [
    ['<!doctype html><title>proxy</title>', 'text/html', 'API_HTML_RESPONSE'],
    ['', 'application/json', 'API_EMPTY_RESPONSE'],
    ['null', 'application/json', 'API_INVALID_JSON'],
    ['{"broken":', 'application/json', 'API_INVALID_JSON'],
  ]) {
    globalThis.fetch = async () => new Response(body, { headers: { 'Content-Type': contentType } });
    await assert.rejects(api('/api/stocks?q=private-search'), error => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.code, code);
      assert.match(error.message, /\/api\/stocks/);
      assert.ok(!error.message.includes('private-search'));
      assert.ok(!error.message.includes('<title>'));
      return true;
    });
  }
});

test('HTTP errors preserve status, validation details and authentication events', async () => {
  let event;
  globalThis.window = { dispatchEvent: value => { event = value; } };
  globalThis.fetch = async () => new Response(JSON.stringify({ code: 'AUTH_REQUIRED', detail: '请登录' }), { status: 401 });
  await assert.rejects(api('/api/watchlist'), { status: 401, code: 'AUTH_REQUIRED', message: '请登录' });
  assert.equal(event.type, 'finpulse:auth-required');
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: [{ loc: ['body', 'username'], msg: '过短' }] }), { status: 422 });
  await assert.rejects(api('/api/auth/login', {}), { status: 422, message: 'username: 过短' });
  globalThis.fetch = async () => new Response('<h1>gateway</h1>', { status: 502 });
  await assert.rejects(api('/api/health'), { status: 502, message: '请求失败（HTTP 502）' });
});

test('requests retain CSRF protection, bypass browser caches and never retry writes', async () => {
  let calls = 0;
  setCsrfToken('fixture-token');
  globalThis.fetch = async (path, options) => {
    calls++;
    assert.equal(path, '/api/watchlist');
    assert.equal(options.method, 'POST');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.headers.Accept, 'application/json');
    assert.equal(options.headers['X-CSRF-Token'], 'fixture-token');
    return new Response('not json');
  };
  await assert.rejects(api('/api/watchlist', { codes: [] }), { code: 'API_INVALID_JSON' });
  assert.equal(calls, 1);
});

test('valid JSON values are returned and interrupted bodies remain visible failures', async () => {
  for (const value of [{ items: [] }, [], false, 0]) {
    globalThis.fetch = async () => new Response(JSON.stringify(value));
    assert.deepEqual(await api('/api/stocks'), value);
  }
  globalThis.fetch = async () => ({ status: 200, ok: true, text: async () => { throw new TypeError('terminated'); } });
  await assert.rejects(api('/api/dashboard/600519'), { code: 'RESPONSE_INTERRUPTED' });
});
