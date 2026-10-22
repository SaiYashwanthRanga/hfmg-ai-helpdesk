/** Wording and row limits for the weekly follow-up groups. Pure, so it can be tested without a browser. */

import type { Followup, VendorItem } from "../../types/ticketReport.ts";

/** Rows shown per group until "Show all" is pressed: a long group must not bury the other two. */
export const FOLLOWUP_ROWS = 10;

export function limitRows<T>(rows: T[], showAll: boolean, limit = FOLLOWUP_ROWS): T[] {
  return showAll ? rows : rows.slice(0, limit);
}

export function vendorStatusLabel(status: VendorItem["status"]): string {
  return status === "waiting_on_vendor" ? "Waiting on vendor" : "Needs review";
}

/** "21 days", "1 day", or "Not available" when the file has no created time. */
export function daysOpenLabel(days: number | null): string {
  if (days === null) return "Not available";
  return days === 1 ? "1 day" : `${days} days`;
}

/**
 * Something to tell the user when the vendor / provider pass did not fully work, so an empty group is never read as
 * "nothing is waiting on a vendor". Undefined when the groups are complete (or there was nothing to classify).
 */
export function followupNote(followup: Followup): string | undefined {
  if (followup.status === "unavailable") {
    return "The vendor and provider groups could not be determined this time, so they are empty. The priority issues below are not affected.";
  }
  if (followup.status === "partial") {
    return `The vendor and provider groups cover ${followup.classified} tickets; the rest could not be classified, so these groups may be incomplete.`;
  }
  return undefined;
}
