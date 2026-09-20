import { X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "../../lib/useFocusTrap";
import { Button } from "./Button";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  width?: number;
  children: ReactNode;
}

/**
 * Base slide-over primitive underlying TicketDrawer/CallDrawer
 * (DESIGN_SYSTEM.md §14). Slides in from the right on desktop; becomes
 * full-screen with no backdrop below the `md` breakpoint (DESIGN.md §16).
 */
export function Drawer({ open, onClose, title, width = 480, children }: DrawerProps) {
  const containerRef = useFocusTrap<HTMLDivElement>(open, onClose);

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-50">
          <motion.div
            className="absolute inset-0 bg-overlay md:backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.24 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.div
            ref={containerRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="drawer-title"
            tabIndex={-1}
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ duration: 0.24, ease: "easeOut" }}
            style={{ maxWidth: width }}
            className="absolute inset-y-0 right-0 flex w-full flex-col border-l border-border bg-card shadow-lg md:rounded-l-lg"
          >
            <div className="flex shrink-0 items-center justify-between gap-4 border-b border-border px-6 py-4">
              <h2 id="drawer-title" className="text-lg font-semibold text-foreground">
                {title}
              </h2>
              <Button variant="icon" aria-label="Close panel" onClick={onClose}>
                <X className="size-4" aria-hidden="true" />
              </Button>
            </div>
            <div className="flex-1 overflow-y-auto">{children}</div>
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
