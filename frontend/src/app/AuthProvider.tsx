import { Loader2 } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { authApi, type Session } from "../api/auth";
import { setUnauthorizedHandler } from "../api/http";
import { ErrorState } from "../components/ui/ErrorState";
import { queryClient } from "../lib/queryClient";
import { LoginPage } from "../pages/LoginPage";

type AuthState =
  | { status: "checking" }
  | { status: "unreachable"; message: string }
  | { status: "signed-out"; expired: boolean }
  | { status: "signed-in"; session: Session };

interface AuthContextValue {
  session: Session;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Gate in front of the whole dashboard: shows the sign-in page until the
 * backend has a session for this browser, and again as soon as any API call
 * answers 401 (session expired, or signed out in another tab). Cached data
 * is dropped on sign-out so the next person never sees the last one's tickets.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "checking" });

  const loadSession = useCallback(() => {
    authApi.me().then(
      (session) => setState(session ? { status: "signed-in", session } : { status: "signed-out", expired: false }),
      (error: unknown) =>
        setState({ status: "unreachable", message: error instanceof Error ? error.message : "Unknown error" }),
    );
  }, []);

  useEffect(loadSession, [loadSession]);

  const retry = useCallback(() => {
    setState({ status: "checking" });
    loadSession();
  }, [loadSession]);

  useEffect(() => {
    setUnauthorizedHandler(() =>
      setState((current) => (current.status === "signed-in" ? { status: "signed-out", expired: true } : current)),
    );
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    if (state.status === "signed-out") queryClient.clear();
  }, [state.status]);

  const signOut = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // The cookie may survive an unreachable server; the page still signs out.
    }
    setState({ status: "signed-out", expired: false });
  }, []);

  if (state.status === "checking") {
    return (
      <div role="status" aria-label="Loading" className="flex h-dvh items-center justify-center bg-[#FBFBF9]">
        <Loader2 className="size-5 animate-spin text-stone-400" aria-hidden="true" />
      </div>
    );
  }

  if (state.status === "unreachable") {
    return (
      <div className="flex h-dvh items-center justify-center bg-[#FBFBF9] px-4">
        <ErrorState severity="full-outage" title="Can't reach the help desk server" description={state.message} retry={retry} />
      </div>
    );
  }

  if (state.status === "signed-out") {
    return (
      <LoginPage expired={state.expired} onSignedIn={(session) => setState({ status: "signed-in", session })} />
    );
  }

  return <AuthContext.Provider value={{ session: state.session, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
