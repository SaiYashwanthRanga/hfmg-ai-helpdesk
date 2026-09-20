import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { createPortal } from "react-dom";
import { useSidebar } from "../../app/SidebarContext";
import { useFocusTrap } from "../../lib/useFocusTrap";
import { Button } from "../ui/Button";
import { NavItem } from "./NavItem";
import { NAV_ITEMS } from "./navItems";

/**
 * Persistent navigation (DESIGN.md §4/§5). Three responsive states
 * (DESIGN_SYSTEM.md §5): 240px expanded on desktop (≥1280px), a 64px
 * icon-only rail on tablet (768–1279px), and a full-screen overlay on
 * mobile (<768px), triggered by Header's hamburger button.
 */
export function Sidebar() {
  return (
    <>
      {/*
        Desktop (xl, ≥1280px) / tablet (md, 768–1279px) — always visible,
        part of the normal flex layout. `collapsed` makes each NavItem's own
        label responsive (icon-only rail on tablet, full label at xl+), so
        there is exactly one rendered list, not two kept in sync by hand.
      */}
      <nav
        aria-label="Primary"
        className="hidden shrink-0 flex-col gap-1 overflow-y-auto border-r border-border bg-sidebar p-3 md:flex md:w-16 xl:w-60"
      >
        {NAV_ITEMS.map((item) => (
          <NavItem key={item.to} item={item} collapsed />
        ))}
      </nav>

      <MobileSidebar />
    </>
  );
}

function MobileSidebar() {
  const { mobileOpen, closeMobile } = useSidebar();
  const containerRef = useFocusTrap<HTMLDivElement>(mobileOpen, closeMobile);

  return createPortal(
    <AnimatePresence>
      {mobileOpen ? (
        <div className="fixed inset-0 z-50 md:hidden">
          <motion.div
            className="absolute inset-0 bg-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={closeMobile}
            aria-hidden="true"
          />
          <motion.div
            ref={containerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Primary navigation"
            tabIndex={-1}
            initial={{ x: "-100%" }}
            animate={{ x: 0 }}
            exit={{ x: "-100%" }}
            transition={{ duration: 0.24, ease: "easeOut" }}
            className="absolute inset-y-0 left-0 flex w-72 max-w-[85%] flex-col gap-1 bg-sidebar p-3"
          >
            <div className="mb-2 flex items-center justify-end">
              <Button variant="icon" aria-label="Close navigation" onClick={closeMobile}>
                <X className="size-4" aria-hidden="true" />
              </Button>
            </div>
            {NAV_ITEMS.map((item) => (
              <NavItem key={item.to} item={item} onNavigate={closeMobile} />
            ))}
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
