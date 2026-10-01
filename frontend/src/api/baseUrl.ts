/**
 * Where the backend API lives.
 *
 * Default: the same host the dashboard was opened from, on the backend port, so
 * one build works on every server and on a developer laptop:
 *   http://172.22.6.98:9090  ->  http://172.22.6.98:8001/api/v1
 *   http://localhost:5173    ->  http://localhost:8001/api/v1
 *
 * Override with VITE_API_BASE_URL (a full URL, or "/api/v1" behind a reverse
 * proxy) or VITE_API_PORT when the backend does not listen on 8001.
 */
const configured = (import.meta.env.VITE_API_BASE_URL ?? "").trim();
const port = (import.meta.env.VITE_API_PORT ?? "").trim() || "8001";

export const API_BASE_URL: string =
  configured || `${window.location.protocol}//${window.location.hostname}:${port}/api/v1`;
