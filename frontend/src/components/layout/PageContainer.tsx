import type { ReactNode } from "react";

/** Owns max-width, side padding, and section spacing (DESIGN_SYSTEM.md §5). */
export function PageContainer({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex w-full max-w-[1440px] flex-col gap-8 px-4 py-8 sm:px-6 lg:px-8">
      {children}
    </main>
  );
}
