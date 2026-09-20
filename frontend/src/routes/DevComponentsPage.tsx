import { AlertCircle, Inbox, Sparkles } from "lucide-react";
import { useState } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { DataTable, type ColumnDef } from "../components/ui/DataTable";
import { Drawer } from "../components/ui/Drawer";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Input } from "../components/ui/Input";
import { LoadingState } from "../components/ui/LoadingState";
import { Modal } from "../components/ui/Modal";
import { Pagination } from "../components/ui/Pagination";
import { SearchBar } from "../components/ui/SearchBar";
import { Select } from "../components/ui/Select";
import { StatusIndicator } from "../components/ui/StatusIndicator";
import { Textarea } from "../components/ui/Textarea";
import { toast } from "../lib/toastStore";

interface DemoRow {
  id: string;
  name: string;
}

const DEMO_ROWS: DemoRow[] = [
  { id: "1", name: "HFMG-2026-000482" },
  { id: "2", name: "HFMG-2026-000481" },
];

const DEMO_COLUMNS: ColumnDef<DemoRow>[] = [{ id: "name", header: "Ticket", render: (row) => row.name }];

/**
 * Internal-only playground for reviewing every Phase 1 primitive in
 * isolation, in its documented states (FRONTEND_IMPLEMENTATION_PLAN.md
 * Phase 1 acceptance criteria). Not linked from navigation.
 */
export function DevComponentsPage() {
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <div className="flex flex-col gap-10">
      <h1 className="text-2xl font-bold text-foreground">Component Playground</h1>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Buttons</h2>
        <div className="flex flex-wrap gap-3">
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="icon" aria-label="Sparkles">
            <Sparkles className="size-4" />
          </Button>
          <Button variant="primary" loading>
            Loading
          </Button>
          <Button variant="primary" disabled>
            Disabled
          </Button>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Badges</h2>
        <div className="flex flex-wrap gap-2">
          <Badge label="New" color="info" />
          <Badge label="Open" color="primary" />
          <Badge label="Resolved" color="success" />
          <Badge label="Urgent" color="danger" />
          <Badge label="AI Summary" color="ai-accent" />
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Status Indicators</h2>
        <div className="flex flex-wrap gap-4">
          <StatusIndicator status="operational" label="OpenAI" />
          <StatusIndicator status="degraded" label="Twilio" />
          <StatusIndicator status="down" label="Database" />
          <StatusIndicator status="unknown" label="Email" />
        </div>
      </section>

      <section className="grid max-w-md flex-col gap-4">
        <h2 className="text-lg font-semibold text-foreground">Forms</h2>
        <Input label="Caller name" placeholder="Maria Lopez" />
        <Input label="Email" error="Enter a valid email address" defaultValue="not-an-email" />
        <Textarea label="Description" placeholder="Describe the issue…" />
        <Select label="Priority" options={[{ label: "High", value: "HIGH" }]} />
        <SearchBar value={search} onChange={setSearch} placeholder="Search tickets…" />
        <SearchBar value="" onChange={() => {}} disabled disabledReason="Search coming soon" />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Cards</h2>
        <Card>Default card</Card>
        <Card clickable>Clickable card (hover me)</Card>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Loading states</h2>
        <LoadingState variant="card" />
        <LoadingState variant="table-rows" count={3} />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Empty &amp; error states</h2>
        <EmptyState icon={Inbox} title="No tickets yet" description="Tickets will appear here." action={{ label: "New Ticket", onClick: () => {} }} />
        <ErrorState severity="degraded" title="OpenAI unreachable" description="AI summaries are paused. Tickets still work normally." retry={() => {}} />
        <ErrorState severity="full-outage" title="Database unreachable" description="The system cannot serve any pages right now." />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Data table &amp; pagination</h2>
        <DataTable
          columns={DEMO_COLUMNS}
          rows={DEMO_ROWS}
          rowKey={(row) => row.id}
          emptyState={<EmptyState icon={AlertCircle} title="No rows" />}
        />
        <Pagination page={1} pageSize={25} total={132} onPageChange={() => {}} onPageSizeChange={() => {}} />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Modal &amp; Drawer</h2>
        <div className="flex gap-3">
          <Button variant="secondary" onClick={() => setModalOpen(true)}>
            Open modal
          </Button>
          <Button variant="secondary" onClick={() => setDrawerOpen(true)}>
            Open drawer
          </Button>
        </div>
        <Modal open={modalOpen} onClose={() => setModalOpen(false)} variant="confirmation" title="Confirm action">
          <p className="text-sm text-muted-foreground">This is a confirmation modal body.</p>
        </Modal>
        <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Example drawer">
          <div className="p-6 text-sm text-muted-foreground">Drawer content.</div>
        </Drawer>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Toasts</h2>
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => toast.success("Ticket status updated")}>
            Success toast
          </Button>
          <Button variant="secondary" onClick={() => toast.warning("Email delivery delayed")}>
            Warning toast
          </Button>
          <Button variant="secondary" onClick={() => toast.error("Failed to save changes")}>
            Error toast
          </Button>
          <Button variant="secondary" onClick={() => toast.info("New ticket created")}>
            Info toast
          </Button>
        </div>
      </section>
    </div>
  );
}
