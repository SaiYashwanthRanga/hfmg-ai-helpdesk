import { X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "../../lib/useFocusTrap";
import { Button } from "./Button";
import { cn } from "../../lib/cn";

export type ModalVariant = "confirmation" | "delete" | "escalation" | "error";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  variant: ModalVariant;
  title: string;
  children: ReactNode;
}

const MAX_WIDTH: Record<ModalVariant, string> = {
  confirmation: "max-w-[420px]",
  delete: "max-w-[420px]",
  escalation: "max-w-[480px]",
  error: "max-w-[480px]",
};

/**
 * Base overlay primitive (DESIGN_SYSTEM.md §15). `delete` never closes on
 * Escape/backdrop click — destructive actions require an explicit button
 * press.
 */
export function Modal({ open, onClose, variant, title, children }: ModalProps) {
  const dismissible = variant !== "delete";
  const containerRef = useFocusTrap<HTMLDivElement>(open, dismissible ? onClose : () => {});

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div
            className="absolute inset-0 bg-overlay backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            onClick={dismissible ? onClose : undefined}
            aria-hidden="true"
          />
          <motion.div
            ref={containerRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
            tabIndex={-1}
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className={cn(
              "relative w-full rounded-lg border border-border bg-card p-6 shadow-lg",
              MAX_WIDTH[variant],
            )}
          >
            <div className="mb-4 flex items-start justify-between gap-4">
              <h2 id="modal-title" className="text-lg font-semibold text-foreground">
                {title}
              </h2>
              <Button variant="icon" aria-label="Close dialog" onClick={onClose}>
                <X className="size-4" aria-hidden="true" />
              </Button>
            </div>
            {children}
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
