/**
 * The anonymous guest session token (HMAC-signed by the server, with its own
 * token budget). Kept in localStorage: it grants nothing but guest chat.
 */
import { api } from "../../lib/api";

const KEY = "nb_guest_session";
const EARLY_RENEWAL_MS = 60_000;

interface StoredToken {
  token: string;
  expiresAt: number;
}

function read(): StoredToken | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredToken;
    return typeof parsed.token === "string" && typeof parsed.expiresAt === "number"
      ? parsed
      : null;
  } catch {
    return null;
  }
}

let memoryToken: StoredToken | null = null;
let pending: Promise<string> | null = null;

export function forgetGuestToken(): void {
  memoryToken = null;
  try {
    localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}

/** A valid guest token, creating a session when needed (one request at a time). */
export async function guestToken(): Promise<string> {
  const stored = memoryToken ?? read();
  if (stored && stored.expiresAt - EARLY_RENEWAL_MS > Date.now()) return stored.token;
  if (!pending) {
    pending = api
      .createGuestSession()
      .then((session) => {
        memoryToken = {
          token: session.session_token,
          expiresAt: new Date(session.expires_at).getTime(),
        };
        try {
          localStorage.setItem(KEY, JSON.stringify(memoryToken));
        } catch {
          // storage blocked: keep it in memory only
        }
        return session.session_token;
      })
      .finally(() => {
        pending = null;
      });
  }
  return pending;
}
