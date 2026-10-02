let csrfToken: string | null = null;
export const setCsrfToken = (token: string | null) => { csrfToken = token; };
export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); }
}

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new Error('本地服务无法连接，请检查后端是否正在运行。');
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = typeof payload?.detail === 'string' ? payload.detail : response.status === 422 ? '填写的信息格式不正确，请检查后重试。' : `请求失败（HTTP ${response.status}）`;
    if (!path.startsWith('/api/auth/') && (payload?.code === 'AUTH_REQUIRED' || payload?.code === 'ACCOUNT_DISABLED')) {
      window.dispatchEvent(new CustomEvent('finpulse:auth-required', { detail: message }));
    }
    throw new ApiError(message, response.status, payload?.code);
  }
  if (!payload) throw new Error('接口返回格式异常。');
  return payload as T;
}
