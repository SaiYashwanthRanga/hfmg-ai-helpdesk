import { Bot } from "lucide-react";
import { useState, type FormEvent } from "react";
import { authApi, type Session } from "../api/auth";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";

export interface LoginPageProps {
  /** True when an API call found the session gone, rather than a first visit. */
  expired: boolean;
  onSignedIn: (session: Session) => void;
}

/** Sign-in with the HFMG email and password (checked by the org login server, via the backend). */
export function LoginPage({ expired, onSignedIn }: LoginPageProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      onSignedIn(await authApi.login(email.trim(), password));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign-in failed. Try again.");
      setPassword("");
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-[#FBFBF9] px-4 py-12 text-stone-900">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <div className="flex size-11 items-center justify-center rounded-xl border border-emerald-900/10 bg-emerald-50 text-emerald-800 shadow-2xs">
            <Bot className="size-5.5" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight">HFMG AI Help Desk</h1>
            <p className="mt-1 text-sm text-stone-500">Sign in with your HFMG email and password.</p>
          </div>
        </div>

        <form
          onSubmit={submit}
          noValidate
          className="flex flex-col gap-4 rounded-xl border border-stone-200/80 bg-white p-6 shadow-xs"
        >
          {expired && !error ? (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              Your session has ended. Sign in again to continue.
            </p>
          ) : null}
          {error ? (
            <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
              {error}
            </p>
          ) : null}

          <Input
            label="Email"
            type="email"
            name="email"
            autoComplete="username"
            inputMode="email"
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={submitting}
          />
          <Input
            label="Password"
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={submitting}
          />
          <Button type="submit" loading={submitting} className="mt-2 w-full">
            Sign in
          </Button>
        </form>
      </div>
    </main>
  );
}
