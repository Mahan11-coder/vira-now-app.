const raw = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || '';

/** Production builds fail without HTTPS API. Dev falls back to localhost. */
export const API_BASE =
  raw ||
  (import.meta.env.DEV ? 'http://localhost:8000' : 'https://api.blackcreator.ir');

export function assertApiConfigured(): void {
  if (!import.meta.env.DEV && (!API_BASE.startsWith('https://') || /localhost|127\.0\.0\.1/i.test(API_BASE))) {
    throw new Error('VITE_API_BASE_URL باید یک آدرس HTTPS عمومی باشد.');
  }
}
