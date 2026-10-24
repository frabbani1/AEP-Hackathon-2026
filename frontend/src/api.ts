import type { RiskResult } from './types';

/** Both are injected at build time by scripts/amplify_deploy.sh (or .env.local in development). */
export const API_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '');
export const ASSETS_URL = (import.meta.env.VITE_ASSETS_URL ?? '').replace(/\/$/, '');

export class ApiError extends Error {}

const GENERIC = 'We could not get your result. Please try again in a moment.';

export async function fetchRisk(token: string, severity: string, signal?: AbortSignal): Promise<RiskResult> {
  if (!API_URL) throw new ApiError('This site is not connected to the service yet.');

  let res: Response;
  try {
    const query = new URLSearchParams({ token, severity });
    res = await fetch(`${API_URL}/risk?${query}`, { signal, headers: { accept: 'application/json' } });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError(GENERIC);
  }

  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (body as { error?: { message?: string } } | null)?.error?.message;
    throw new ApiError(message ?? GENERIC);
  }
  return body as RiskResult;
}
