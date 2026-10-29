import { API_BASE_URL } from "./baseUrl";

/**
 * Every dashboard call to the API goes through here: it sends the session
 * cookie (the API is on another port, so `credentials: "include"` is needed)
 * and reports a 401 to the auth provider, which shows the sign-in page.
 */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${API_BASE_URL}${path}`, { credentials: "include", ...init });
  if (response.status === 401) onUnauthorized?.();
  return response;
}
