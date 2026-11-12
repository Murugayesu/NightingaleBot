import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import { App } from "../app/App";
import type { AppConfig, User } from "../lib/types";
import { json } from "./sse";

export const CONFIG: AppConfig = {
  knowledge_base_name: "TNNMC Nursing Knowledge Base",
  authority_name: "Tamil Nadu Nurses and Midwives Council",
  authority_short_name: "TNNMC",
  contact_url: "https://example.org/contact",
  welcome_message: "Ask about registration.",
  sign_in_methods: ["password"],
  signup_enabled: true,
  guest_chat_enabled: true,
  general_answers_enabled: true,
  general_answer_label:
    "General nursing knowledge: not from official TNNMC records. Verify important details.",
  password_min_length: 10,
  require_session: true,
  max_query_chars: 4000,
  max_context_turns: 3,
  guest_token_budget: 50000,
  user_daily_token_quota: 200000,
};

export const USER: User = {
  id: "u1",
  email: "priya@example.org",
  display_name: "Priya",
  role: "user",
  must_change_password: false,
  created_at: "2026-11-01T00:00:00Z",
};

export type Route = (request: { method: string; url: string; body: unknown; headers: Headers }) =>
  | Response
  | Promise<Response>
  | undefined;

export interface Recorded {
  method: string;
  url: string;
  body: unknown;
  headers: Headers;
}

/** Stub fetch with route handlers; unmatched calls fail loudly. */
export function mockApi(routes: Route[]): Recorded[] {
  const calls: Recorded[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      const method = (init.method ?? "GET").toUpperCase();
      let body: unknown = init.body;
      if (typeof init.body === "string") {
        try {
          body = JSON.parse(init.body);
        } catch {
          body = init.body;
        }
      }
      const call = { method, url, body, headers: new Headers(init.headers) };
      calls.push(call);
      for (const route of routes) {
        const response = await route(call);
        if (response) return response;
      }
      throw new Error(`Unexpected request: ${method} ${url}`);
    }),
  );
  return calls;
}

export const configRoute: Route = ({ url }) =>
  url === "/api/v1/app/config" ? json(CONFIG) : undefined;

export const guestMeRoute: Route = ({ url }) =>
  url === "/api/v1/auth/session" ? json({ user: null }) : undefined;

export const userMeRoute =
  (user: User = USER): Route =>
  ({ url }) =>
    url === "/api/v1/auth/session" ? json({ user }) : undefined;

export function renderApp(path = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
