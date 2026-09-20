import { AlertTriangle, CheckCircle2, Info, OctagonAlert, X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { createPortal } from "react-dom";
import { toast, useToasts, type ToastItem, type ToastVariant } from "../../lib/toastStore";
import { cn } from "../../lib/cn";

const VARIANT_ICON: Record<ToastVariant, LucideIcon> = {
  success: CheckCircle2,
  warning: AlertTriangle,
  error: OctagonAlert,
  info: Info,
};

const VARIANT_BORDER: Record<ToastVariant, string> = {
  success: "border-l-success",
  warning: "border-l-warning",
  error: "border-l-danger",
  info: "border-l-info",
};

const VARIANT_ICON_COLOR: Record<ToastVariant, string> = {
  success: "text-success",
  warning: "text-warning",
  error: "text-danger",
  info: "text-info",
};

const MAX_VISIBLE = 3;

function Toast({ item }: { item: ToastItem }) {
  const Icon = VARIANT_ICON[item.variant];

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 24 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      role={item.variant === "error" ? "alert" : "status"}
      className={cn(
        "pointer-events-auto flex w-80 items-start gap-3 rounded-md border-l-4 bg-card p-3 shadow-md",
        VARIANT_BORDER[item.variant],
      )}
    >
      <Icon className={cn("mt-0.5 size-4 shrink-0", VARIANT_ICON_COLOR[item.variant])} aria-hidden="true" />
      <div className="flex-1 text-sm text-foreground">
        {item.message}
        {item.action ? (
          <button
            type="button"
            onClick={item.action.onClick}
            className="ml-2 font-medium text-primary hover:underline"
          >
            {item.action.label}
          </button>
        ) : null}
      </div>
      <button
        type="button"
        aria-label="Dismiss notification"
        onClick={() => toast.dismiss(item.id)}
        className="text-muted-foreground hover:text-foreground"
      >
        <X className="size-4" aria-hidden="true" />
      </button>
    </motion.div>
  );
}

/**
 * Toast notification stack (DESIGN_SYSTEM.md §16). Purely client-side —
 * subscribes to lib/toastStore, no API dependency. Renders as a
 * fixed top-right overlay regardless of where it's mounted in the tree.
 */
export function NotificationCenter() {
  const toasts = useToasts();
  const visible = toasts.slice(-MAX_VISIBLE);
  const overflowCount = toasts.length - visible.length;

  return createPortal(
    <div
      className="pointer-events-none fixed top-4 right-4 z-[100] flex flex-col items-end gap-2"
      aria-live="polite"
    >
      {overflowCount > 0 ? (
        <span className="pointer-events-auto rounded-pill bg-card-hover px-3 py-1 text-xs text-muted-foreground">
          +{overflowCount} more
        </span>
      ) : null}
      <AnimatePresence>
        {visible.map((item) => (
          <Toast key={item.id} item={item} />
        ))}
      </AnimatePresence>
    </div>,
    document.body,
  );
}
