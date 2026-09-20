import { Bot, Database, Mail, Phone } from "lucide-react";
import { useSettingsStatusQuery } from "../api/settings";
import { ApiError } from "../api/client";
import { EnvironmentStatusCard } from "../components/settings/EnvironmentStatusCard";
import { ProviderStatusCard } from "../components/settings/ProviderStatusCard";
import { ReadOnlyConfigPanel } from "../components/settings/ReadOnlyConfigPanel";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";

/**
 * Settings — read-only (DESIGN.md §12). GET /settings/status only. No
 * write path exists anywhere on this page, and none should be added until
 * Phase 3 backend authentication ships — see ReadOnlyConfigPanel.
 */
export function SettingsPage() {
  const { data, isLoading, isError, error, refetch } = useSettingsStatusQuery();

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-foreground">Settings</h1>
      <p className="text-sm text-muted-foreground">
        Read-only configuration visibility. Editing configuration here isn't supported and won't be until
        authentication exists for this system.
      </p>

      {isLoading ? (
        <LoadingState variant="page" />
      ) : isError ? (
        <ErrorState
          severity="degraded"
          title="Couldn't load settings status"
          description={error instanceof ApiError ? error.message : "Something went wrong. Please try again."}
          retry={() => refetch()}
        />
      ) : data ? (
        <ReadOnlyConfigPanel>
          <ProviderStatusCard icon={Bot} label="OpenAI" data={data.openai} />
          <ProviderStatusCard icon={Phone} label="Twilio" data={data.twilio} />
          <ProviderStatusCard icon={Mail} label="Email (SendGrid)" data={data.email} />
          <ProviderStatusCard icon={Database} label="Database" data={data.database} />
          <EnvironmentStatusCard data={data.environment} />
        </ReadOnlyConfigPanel>
      ) : null}
    </div>
  );
}
