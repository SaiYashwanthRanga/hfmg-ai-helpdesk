import { Phone } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { useVoiceCallsQuery } from "../api/voiceCalls";
import { ApiError } from "../api/client";
import { CallDrawer } from "../components/calls/CallDrawer";
import { CallStatsPanel } from "../components/calls/CallStatsPanel";
import { CallTable } from "../components/calls/CallTable";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Pagination } from "../components/ui/Pagination";
import type { VoiceCallListItem } from "../types/voiceCall";

const PAGE_SIZE = 25;

/**
 * Voice Operations Center (WIREFRAMES.md §5). Page/open-call are URL
 * state, mirroring TicketsPage's `?ticket=` pattern exactly (Phase 3) so
 * the drawer never causes a route navigation.
 */
export function CallsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Number(searchParams.get("page") ?? "1");
  const openCallId = searchParams.get("call");

  const callsQuery = useVoiceCallsQuery({ page, page_size: PAGE_SIZE });

  function updateParams(patch: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    setSearchParams(next);
  }

  function openCall(call: VoiceCallListItem) {
    updateParams({ call: call.id });
  }

  function closeCall() {
    updateParams({ call: null });
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-foreground">Calls</h1>

      <CallStatsPanel />

      {callsQuery.isError ? (
        <ErrorState
          severity="degraded"
          title="Couldn't load calls"
          description={callsQuery.error instanceof ApiError ? callsQuery.error.message : "Something went wrong. Please try again."}
          retry={() => callsQuery.refetch()}
        />
      ) : (
        <>
          <CallTable
            calls={callsQuery.data?.items ?? []}
            isLoading={callsQuery.isLoading}
            onRowClick={openCall}
            emptyState={
              <EmptyState
                icon={Phone}
                title="No calls yet"
                description="Calls will appear here once the Twilio number receives calls."
              />
            }
          />

          {callsQuery.data && callsQuery.data.total > 0 ? (
            <Pagination
              page={callsQuery.data.page}
              pageSize={PAGE_SIZE}
              total={callsQuery.data.total}
              onPageChange={(nextPage) => updateParams({ page: String(nextPage) })}
            />
          ) : null}
        </>
      )}

      <CallDrawer callId={openCallId} onClose={closeCall} />
    </div>
  );
}
