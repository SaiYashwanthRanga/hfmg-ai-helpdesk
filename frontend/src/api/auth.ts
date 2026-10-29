import { API_BASE_URL } from "./baseUrl";
import { ApiError } from "./client";

export interface Session {
  email: string;
  name: string;
  /** False when the backend runs with AUTH_REQUIRED=false: no sign-in, no "Sign out". */
  login_required: boolean;
}

// Plain fetch, not apiFetch: a 401 here is an answer (wrong password, not signed
// in), not a session that just ended.
async function call(method: string, path: string, body?: unknown): Promise<Response> {
  try {
    return await fetch(`${API_BASE_URL}/auth${path}`, {
      method,
      credentials: "include",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Could not reach the server. Check the connection and try again.", 0);
  }
}

async function detail(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (typeof payload?.detail === "string") return payload.detail;
  } catch {
    // not JSON; fall back to statusText
  }
  return response.statusText || `Request failed (${response.status})`;
}

export const authApi = {
  /** The signed-in session, or null when nobody is signed in. */
  me: async (): Promise<Session | null> => {
    const response = await call("GET", "/me");
    if (response.status === 401) return null;
    if (!response.ok) throw new ApiError(await detail(response), response.status);
    return (await response.json()) as Session;
  },

  login: async (email: string, password: string): Promise<Session> => {
    const response = await call("POST", "/login", { email, password });
    if (!response.ok) throw new ApiError(await detail(response), response.status);
    return (await response.json()) as Session;
  },

  logout: async (): Promise<void> => {
    await call("POST", "/logout");
  },
};
