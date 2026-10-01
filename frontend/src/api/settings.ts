import { useQuery } from "@tanstack/react-query";
import type { SettingsStatusResponse } from "../types/settings";
import { ApiError } from "./client";
import { API_BASE_URL } from "./baseUrl";


/**
 * GET /settings/status only — there is deliberately no corresponding
 * write function in this module (no `updateSettings`, no `PATCH`/`POST`).
 * DESIGN.md §12: editing OPENAI_API_KEY/VOICE_SIP_GATEWAY_TOKEN/SENDGRID_API_KEY
 * with no auth in front of this API is a live vulnerability, not a rough
 * edge. Do not add one here without Phase 3 backend auth landing first.
 */
export async function fetchSettingsStatus(): Promise<SettingsStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/settings/status`);
  if (!response.ok) {
    throw new ApiError(response.statusText, response.status);
  }
  return (await response.json()) as SettingsStatusResponse;
}

export function useSettingsStatusQuery() {
  return useQuery({ queryKey: ["settings", "status"], queryFn: fetchSettingsStatus, refetchInterval: 30_000 });
}
