import type { DependencyStatus } from "../components/ui/StatusIndicator";

/**
 * Read-only provider status (DESIGN.md §12). `masked_key` is either a
 * masked secret (e.g. "sk-...a1b2") or null — this type must never gain a
 * field capable of holding a raw secret, and no component consuming it may
 * add an editable control.
 */
export interface ProviderStatus {
  configured: boolean;
  status: DependencyStatus;
  masked_key: string | null;
  detail: string | null;
  last_verified: string;
}

export interface EnvironmentStatus {
  environment: string;
  enable_ai_summary: boolean;
  enable_email_notifications: boolean;
}

export interface SettingsStatusResponse {
  openai: ProviderStatus;
  twilio: ProviderStatus;
  email: ProviderStatus;
  database: ProviderStatus;
  environment: EnvironmentStatus;
}
