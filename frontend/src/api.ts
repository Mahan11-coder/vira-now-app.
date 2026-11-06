import { API_BASE } from './env';

const TOKEN_KEY = 'vira_token';
const ADMIN_TOKEN_KEY = 'vira_admin_token';

export type ApiError = Error & { status?: number; kind?: 'network' | 'http' | 'auth' };

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getAdminToken(): string {
  return localStorage.getItem(ADMIN_TOKEN_KEY) || '';
}

export function setAdminToken(token: string | null): void {
  if (token) localStorage.setItem(ADMIN_TOKEN_KEY, token);
  else localStorage.removeItem(ADMIN_TOKEN_KEY);
}

export async function api<T = any>(
  path: string,
  options: RequestInit & { auth?: 'user' | 'admin' | 'none' } = {}
): Promise<T> {
  const { auth = 'user', headers: extra, ...rest } = options;
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(extra as Record<string, string>),
  };
  if (rest.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';

  if (auth === 'user' && getToken()) headers.Authorization = `Bearer ${getToken()}`;
  if (auth === 'admin' && getAdminToken()) headers.Authorization = `Bearer ${getAdminToken()}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...rest, headers });
  } catch {
    const err = new Error('ارتباط با سرور برقرار نشد. اتصال اینترنت را بررسی کن.') as ApiError;
    err.kind = 'network';
    throw err;
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error((data as any)?.detail || `خطای سرور (${res.status})`) as ApiError;
    err.status = res.status;
    err.kind = res.status === 401 ? 'auth' : 'http';
    if (res.status === 401 && auth === 'user') setToken(null);
    if (res.status === 401 && auth === 'admin') setAdminToken(null);
    throw err;
  }
  return data as T;
}

export const publicApi = {
  config: () => api<Record<string, string>>('/api/public/config', { auth: 'none' }),
  services: () => api<any[]>('/api/public/services', { auth: 'none' }),
  plans: () => api<any[]>('/api/public/subscription-plans', { auth: 'none' }),
};

export const authApi = {
  start: () =>
    api<{ session_id: string; poll_secret: string; bot_url: string; expires_in: number }>(
      '/api/auth/telegram/start',
      { method: 'POST', body: '{}', auth: 'none' }
    ),
  status: (session_id: string, poll_secret: string) =>
    api<{ status: string; access_token?: string; user?: any }>(
      '/api/auth/telegram/status',
      {
        method: 'POST',
        body: JSON.stringify({ session_id, poll_secret }),
        auth: 'none',
      }
    ),
  me: () => api<any>('/api/auth/me'),
  logout: () => api('/api/auth/logout', { method: 'POST' }),
  deleteAccount: () => api('/api/auth/account', { method: 'DELETE' }),
};

export const channelApi = {
  list: () => api<any[]>('/api/channels'),
  verify: (chat_ref: string) =>
    api('/api/channels/verify', { method: 'POST', body: JSON.stringify({ chat_ref }) }),
  remove: (id: number) => api(`/api/channels/${id}`, { method: 'DELETE' }),
  posts: (id: number) => api<any[]>(`/api/channels/${id}/posts`),
  createPost: (id: number, text: string) =>
    api(`/api/channels/${id}/posts`, { method: 'POST', body: JSON.stringify({ text }) }),
  publish: (postId: string) =>
    api(`/api/channels/posts/${postId}/publish`, { method: 'POST', body: '{}' }),
  deletePost: (postId: string) => api(`/api/channels/posts/${postId}`, { method: 'DELETE' }),
};

export const walletApi = {
  subscriptions: () => api<any>('/api/subscriptions'),
  purchase: (plan_code: string) =>
    api('/api/subscriptions/purchase', { method: 'POST', body: JSON.stringify({ plan_code }) }),
  createInvoice: (amount_toman: number) =>
    api<{ id: number; pay_url: string }>('/api/payments/invoices', {
      method: 'POST',
      body: JSON.stringify({ amount_toman }),
    }),
  redeem: (code: string) =>
    api('/api/payments/redeem', { method: 'POST', body: JSON.stringify({ code }) }),
};

export const aiApi = {
  generate: (prompt: string) =>
    api<{ text: string }>('/api/ai/generate', {
      method: 'POST',
      body: JSON.stringify({ prompt }),
    }),
};

export const adminApi = {
  login: (password: string) =>
    api<{ access_token: string }>('/api/admin/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
      auth: 'none',
    }),
  overview: () => api('/api/admin/overview', { auth: 'admin' }),
  settings: () => api('/api/admin/settings', { auth: 'admin' }),
  saveSettings: (body: Record<string, string>) =>
    api('/api/admin/settings', { method: 'PUT', body: JSON.stringify(body), auth: 'admin' }),
  services: () => api('/api/admin/services', { auth: 'admin' }),
  users: () => api('/api/admin/users', { auth: 'admin' }),
  audit: () => api('/api/admin/audit', { auth: 'admin' }),
};
