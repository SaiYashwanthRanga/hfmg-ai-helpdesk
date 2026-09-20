import { Server } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import type { EnvironmentStatus } from "../../types/settings";

export function EnvironmentStatusCard({ data }: { data: EnvironmentStatus }) {
  return (
    <Card>
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Server className="size-4 text-muted-foreground" aria-hidden="true" />
        Environment
      </h2>
      <dl className="grid grid-cols-[160px_1fr] items-center gap-y-2 text-sm">
        <dt className="text-muted-foreground">Environment</dt>
        <dd>
          <Badge label={data.environment} color={data.environment === "production" ? "success" : "muted"} size="sm" />
        </dd>
        <dt className="text-muted-foreground">AI summaries</dt>
        <dd>
          <Badge label={data.enable_ai_summary ? "Enabled" : "Disabled"} color={data.enable_ai_summary ? "success" : "muted"} size="sm" />
        </dd>
        <dt className="text-muted-foreground">Email notifications</dt>
        <dd>
          <Badge
            label={data.enable_email_notifications ? "Enabled" : "Disabled"}
            color={data.enable_email_notifications ? "success" : "muted"}
            size="sm"
          />
        </dd>
      </dl>
    </Card>
  );
}
