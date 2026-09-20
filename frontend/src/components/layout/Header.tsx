import { Bot, Menu } from "lucide-react";
import { useSidebar } from "../../app/SidebarContext";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { NotificationCenter } from "./NotificationCenter";
import { StatusBar } from "./StatusBar";

type Environment = "development" | "staging" | "production";

const ENV_LABEL: Record<Environment, string> = {
  development: "dev",
  staging: "staging",
  production: "production",
};

function resolveEnvironment(): Environment {
  const configured = import.meta.env.VITE_ENVIRONMENT as Environment | undefined;
  if (configured === "staging" || configured === "production") return configured;
  return "development";
}

/**
 * Product identity, environment indicator, and the always-visible System
 * Status strip (DESIGN.md §5). 64px fixed height at every breakpoint.
 */
export function Header() {
  const environment = resolveEnvironment();
  const { openMobile } = useSidebar();

  return (
    <header className="sticky top-0 z-40 flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border bg-sidebar px-4 sm:px-6">
      <div className="flex items-center gap-3">
        <Button variant="icon" aria-label="Open navigation" onClick={openMobile} className="md:hidden">
          <Menu className="size-5" aria-hidden="true" />
        </Button>
        <Bot className="size-5 text-ai-accent" aria-hidden="true" />
        <span className="text-sm font-semibold text-foreground">HFMG AI Help Desk</span>
        <Badge label={ENV_LABEL[environment]} color={environment === "production" ? "success" : "muted"} size="sm" />
      </div>

      <div className="hidden md:block">
        <StatusBar />
      </div>

      <NotificationCenter />
    </header>
  );
}
