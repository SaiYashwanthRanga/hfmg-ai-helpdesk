import { Bot, Menu } from "lucide-react";
import { Link } from "react-router-dom";
import { useSidebar } from "../../app/SidebarContext";
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
    <header className="sticky top-0 z-40 flex h-16 shrink-0 items-center justify-between gap-4 border-b border-stone-200/80 bg-white/95 px-4 backdrop-blur-md shadow-xs sm:px-6">
      <div className="flex items-center gap-3">
        <Button variant="icon" aria-label="Open navigation" onClick={openMobile} className="md:hidden text-stone-500 hover:text-stone-900">
          <Menu className="size-5" aria-hidden="true" />
        </Button>
        <Link
          to="/"
          className="flex items-center gap-3 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800"
          title="Go to Dashboard"
        >
          <div className="flex size-8 items-center justify-center rounded-lg border border-emerald-900/10 bg-emerald-50 text-emerald-800 shadow-2xs transition-transform duration-150 group-hover:scale-105">
            <Bot className="size-4.5" aria-hidden="true" />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold tracking-tight text-stone-900 hover:text-emerald-900 transition-colors">
              HFMG AI Help Desk
            </span>
            <span className="rounded-full border border-stone-200 bg-stone-100 px-2 py-0.5 text-[11px] font-medium text-stone-600">
              {ENV_LABEL[environment]}
            </span>
          </div>
        </Link>
      </div>

      <div className="flex items-center gap-4">
        <div className="hidden md:block">
          <StatusBar />
        </div>
        <NotificationCenter />
      </div>
    </header>
  );
}

