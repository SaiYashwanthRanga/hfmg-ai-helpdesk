import type { DependencyStatus } from "../components/ui/StatusIndicator";

export interface DependencyHealth {
  status: DependencyStatus;
  lastChecked?: string;
}

export interface DependencyHealthReport {
  openai: DependencyHealth;
  twilio: DependencyHealth;
  database: DependencyHealth;
  email: DependencyHealth;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

interface RawDependencyStatus {
  status: DependencyStatus;
  checked_at: string;
}

interface RawDependencyHealthReport {
  openai: RawDependencyStatus;
  twilio: RawDependencyStatus;
  database: RawDependencyStatus;
  email: RawDependencyStatus;
}

/**
 * `GET /api/v1/health/dependencies` is real (Backend Tier 1 — see
 * WORK_LOG.md). Each dependency is cached server-side for 30s, matching
 * this module's poll interval in StatusBar, so a poll tick either gets a
 * fresh check or an instant cache hit, never a redundant live call.
 */
export async function fetchDependencyHealth(): Promise<DependencyHealthReport> {
  const response = await fetch(`${API_BASE_URL}/health/dependencies`);
  if (!response.ok) {
    // A failed health-of-health check renders as "unknown" everywhere,
    // never a fabricated "operational" — this is the one place `unknown`
    // is still a real, reachable state rather than a permanent fallback.
    const unknown: DependencyHealth = { status: "unknown" };
    return { openai: unknown, twilio: unknown, database: unknown, email: unknown };
  }
  const body = (await response.json()) as RawDependencyHealthReport;
  const map = (d: RawDependencyStatus): DependencyHealth => ({ status: d.status, lastChecked: d.checked_at });
  return { openai: map(body.openai), twilio: map(body.twilio), database: map(body.database), email: map(body.email) };
}
