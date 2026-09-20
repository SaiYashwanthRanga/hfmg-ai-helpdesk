import { Mail, Phone, User } from "lucide-react";

export interface CallerInfoPanelProps {
  callerName: string;
  phoneNumber: string;
  email: string | null;
}

/** Caller Information drawer section (WIREFRAMES.md §4). */
export function CallerInfoPanel({ callerName, phoneNumber, email }: CallerInfoPanelProps) {
  return (
    <section className="flex flex-col gap-2 border-b border-border px-6 py-4">
      <h3 className="text-sm font-semibold text-foreground">Caller Information</h3>
      <div className="flex items-center gap-2 text-sm text-foreground">
        <User className="size-4 text-muted-foreground" aria-hidden="true" />
        {callerName}
      </div>
      <div className="flex items-center gap-2 text-sm text-foreground">
        <Phone className="size-4 text-muted-foreground" aria-hidden="true" />
        {phoneNumber}
      </div>
      <div className="flex items-center gap-2 text-sm text-foreground">
        <Mail className="size-4 text-muted-foreground" aria-hidden="true" />
        {email ?? <span className="text-muted-foreground">Not provided</span>}
      </div>
    </section>
  );
}
