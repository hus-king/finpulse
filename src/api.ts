let csrfToken: string | null = null;
export const setCsrfToken = (token: string | null) => { csrfToken = token; };
export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); }
}

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal, method?: 'POST' | 'PUT' | 'DELETE'): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: method ?? (body === undefined ? 'GET' : 'POST'),
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    signal?.throwIfAborted();
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new Error('服务无法连接，请检查网络或后端是否正在运行。');
  }
  const endpoint = path.split(/[?#]/)[0];
  let text: string;
  try {
    text = await response.text();
  } catch (error) {
    // Navigation and StrictMode cleanup can abort after headers arrive. Keep that
    // cancellation intact instead of turning it into an invalid-JSON warning.
    signal?.throwIfAborted();
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new ApiError(`响应接收中断，请检查网络后重试（${endpoint}）。`, response.status, 'RESPONSE_INTERRUPTED');
  }
  signal?.throwIfAborted();
  let payload;
  try { payload = JSON.parse(text); } catch { /* Handle HTTP errors before diagnosing a successful non-JSON response. */ }
  if (!response.ok) {
    const details = Array.isArray(payload?.detail) ? payload.detail.slice(0, 3).map((item: { loc?: (string | number)[]; msg?: string }) => `${item.loc?.filter(part => part !== 'body').join('.') ?? ''}: ${item.msg ?? '格式无效'}`).join('；') : '';
    const message = typeof payload?.detail === 'string' ? payload.detail : response.status === 422 ? details || '填写的信息格式不正确，请检查后重试。' : `请求失败（HTTP ${response.status}）`;
    if (!path.startsWith('/api/auth/') && (payload?.code === 'AUTH_REQUIRED' || payload?.code === 'ACCOUNT_DISABLED')) {
      window.dispatchEvent(new CustomEvent('finpulse:auth-required', { detail: message }));
    }
    throw new ApiError(message, response.status, payload?.code);
  }
  if (payload == null) {
    const html = response.headers.get('content-type')?.includes('text/html') || /^\s*</.test(text);
    const message = html ? '请求收到了网页内容，请刷新页面；若仍出现，请检查代理或服务入口'
      : !text.trim() ? '服务返回了空响应，请稍后重试' : '服务响应无法解析，请稍后重试';
    throw new ApiError(`${message}（${endpoint}）。`, response.status, html ? 'API_HTML_RESPONSE' : !text.trim() ? 'API_EMPTY_RESPONSE' : 'API_INVALID_JSON');
  }
  return payload as T;
}
