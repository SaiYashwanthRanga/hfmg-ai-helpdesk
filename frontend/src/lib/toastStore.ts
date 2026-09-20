import { useSyncExternalStore } from "react";

export type ToastVariant = "success" | "warning" | "error" | "info";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastItem {
  id: string;
  variant: ToastVariant;
  message: string;
  action?: ToastAction;
}

/** Auto-dismiss timing per DESIGN_SYSTEM.md §16 — errors require manual dismissal. */
const AUTO_DISMISS_MS: Record<ToastVariant, number | null> = {
  success: 5000,
  info: 5000,
  warning: 7000,
  error: null,
};

let toasts: ToastItem[] = [];
const listeners = new Set<() => void>();

function emitChange() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return toasts;
}

function dismiss(id: string) {
  toasts = toasts.filter((t) => t.id !== id);
  emitChange();
}

function push(variant: ToastVariant, message: string, action?: ToastAction) {
  const id = crypto.randomUUID();
  toasts = [...toasts, { id, variant, message, action }];
  emitChange();

  const timeout = AUTO_DISMISS_MS[variant];
  if (timeout !== null) {
    setTimeout(() => dismiss(id), timeout);
  }
  return id;
}

export const toast = {
  success: (message: string, action?: ToastAction) => push("success", message, action),
  warning: (message: string, action?: ToastAction) => push("warning", message, action),
  error: (message: string, action?: ToastAction) => push("error", message, action),
  info: (message: string, action?: ToastAction) => push("info", message, action),
  dismiss,
};

/** Subscribes a component (NotificationCenter) to the current toast stack. */
export function useToasts(): ToastItem[] {
  return useSyncExternalStore(subscribe, getSnapshot);
}
