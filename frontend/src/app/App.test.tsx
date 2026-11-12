import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isInsecureConnection } from "../features/auth/AuthPages";
import { listGuestConversations, resetGuestStoreForTests } from "../features/guest/guestStore";
import {
  USER,
  configRoute,
  guestMeRoute,
  mockApi,
  renderApp,
  userMeRoute,
} from "../test/harness";
import { json, sseResponse } from "../test/sse";

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  resetGuestStoreForTests();
});
afterEach(() => vi.unstubAllGlobals());

const GROUNDED = [
  ["mode", { answer_mode: "grounded", label: "From official records" }],
  [
    "citations",
    [
      {
        source_index: 1,
        chunk_id: "c1",
        document_id: "d1",
        title: "FAQ.pdf",
        page_number: 1,
        score: 0.6,
        snippet: "Fee Rs. 365",
      },
    ],
  ],
  ["token", "The fee is "],
  ["token", "Rs. 365 [Source 1]."],
] as Array<[string, unknown]>;

describe("guest chat", () => {
  it("asks without an account and keeps the chat in this browser", async () => {
    const calls = mockApi([
      configRoute,
      guestMeRoute,
      ({ method, url }) =>
        method === "POST" && url === "/api/v1/chat/session"
          ? json(
              {
                session_id: "s1",
                session_token: "guest-token",
                expires_at: new Date(Date.now() + 86_400_000).toISOString(),
                token_budget: 50000,
              },
              201,
            )
          : undefined,
      ({ method, url }) =>
        method === "POST" && url === "/api/v1/chat"
          ? sseResponse([
              ...GROUNDED,
              [
                "end",
                {
                  final_answer: "The fee is Rs. 365 [Source 1].",
                  answer_mode: "grounded",
                  label: "From official records",
                  citations: (GROUNDED[1]![1] as unknown[]) ?? [],
                  is_grounded: true,
                  gate_passed: true,
                  tokens_used: 120,
                },
              ],
            ])
          : undefined,
    ]);
    renderApp("/");
    const box = await screen.findByRole("textbox", { name: /Ask about registration/ });
    await userEvent.type(box, "What is the registration fee?{Enter}");

    expect(await screen.findByText(/The fee is Rs\. 365/)).toBeInTheDocument();
    expect(await screen.findByText("From official records")).toBeInTheDocument();
    const chat = calls.find((c) => c.url === "/api/v1/chat")!;
    expect(chat.headers.get("X-Session-Token")).toBe("guest-token");
    expect(chat.body).toMatchObject({ query: "What is the registration fee?", stream: true });

    await waitFor(async () => {
      const saved = await listGuestConversations();
      expect(saved).toHaveLength(1);
      expect(saved[0]!.title).toBe("What is the registration fee?");
      expect(saved[0]!.messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    });
    expect(screen.getByText(/Sign in to save this chat/)).toBeInTheDocument();
  });

  it("stops a streaming answer and marks it unchecked", async () => {
    mockApi([
      configRoute,
      guestMeRoute,
      ({ method, url }) =>
        method === "POST" && url === "/api/v1/chat/session"
          ? json(
              {
                session_id: "s1",
                session_token: "t",
                expires_at: new Date(Date.now() + 86_400_000).toISOString(),
                token_budget: 50000,
              },
              201,
            )
          : undefined,
    ]);
    // A stream that sends one token and then waits until it is aborted.
    const fetchMock = vi.mocked(globalThis.fetch);
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      if (String(input) !== "/api/v1/chat") return original(input, init);
      const encoder = new TextEncoder();
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            encoder.encode('event: mode\ndata: {"answer_mode":"grounded","label":"x"}\n\n'),
          );
          controller.enqueue(encoder.encode('event: token\ndata: "Partial answer"\n\n'));
          init.signal?.addEventListener("abort", () =>
            controller.error(new DOMException("Aborted", "AbortError")),
          );
        },
      });
      return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
    });

    renderApp("/");
    await userEvent.type(await screen.findByRole("textbox"), "Tell me about renewal{Enter}");
    expect(await screen.findByText("Partial answer")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByText(/partial answer was not checked/)).toBeInTheDocument();
  });
});

describe("signed-in chat", () => {
  it("creates a conversation, streams a labelled general answer and names the chat", async () => {
    let stored: unknown[] = [];
    const calls = mockApi([
      configRoute,
      userMeRoute(),
      ({ method, url }) =>
        method === "GET" && url.startsWith("/api/v1/conversations?")
          ? json({
              conversations: stored.length
                ? [
                    {
                      id: "c1",
                      title: "Hand hygiene",
                      pinned: false,
                      archived: false,
                      created_at: new Date().toISOString(),
                      updated_at: new Date().toISOString(),
                    },
                  ]
                : [],
              next_cursor: null,
            })
          : undefined,
      ({ method, url }) =>
        method === "POST" && url === "/api/v1/conversations"
          ? json(
              {
                id: "c1",
                title: "New chat",
                pinned: false,
                archived: false,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              },
              201,
            )
          : undefined,
      ({ method, url }) =>
        method === "GET" && url === "/api/v1/conversations/c1"
          ? json({
              id: "c1",
              title: stored.length ? "Hand hygiene" : "New chat",
              pinned: false,
              archived: false,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              messages: stored,
            })
          : undefined,
      ({ method, url }) => {
        if (method !== "POST" || url !== "/api/v1/conversations/c1/messages") return undefined;
        const answer = {
          id: "m2",
          role: "assistant",
          content: "Hand hygiene prevents infections.",
          answer_mode: "general",
          label: "General nursing knowledge: not from official TNNMC records.",
          citations: [],
          status: "complete",
          is_imported: false,
          tokens_used: 50,
          created_at: new Date().toISOString(),
          feedback: null,
        };
        const question = { ...answer, id: "m1", role: "user", content: "What is hand hygiene?", answer_mode: null, label: null };
        stored = [question, answer];
        return sseResponse([
          ["start", { conversation_id: "c1", user_message: question }],
          ["mode", { answer_mode: "general", label: answer.label }],
          ["token", "Hand hygiene "],
          ["token", "prevents infections."],
          [
            "end",
            {
              final_answer: answer.content,
              answer_mode: "general",
              label: answer.label,
              citations: [],
              is_grounded: false,
              gate_passed: false,
              tokens_used: 50,
              message: answer,
              quota_remaining: 199950,
            },
          ],
          ["title", { conversation_id: "c1", title: "Hand hygiene" }],
        ]);
      },
    ]);
    renderApp("/");
    await userEvent.type(await screen.findByRole("textbox", { name: /Ask about/ }), "What is hand hygiene?{Enter}");

    expect(await screen.findByText("Hand hygiene prevents infections.")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("not from official TNNMC records");
    expect(await screen.findByRole("link", { name: "Hand hygiene" })).toHaveAttribute("href", "/c/c1");
    const ask = calls.find((c) => c.url === "/api/v1/conversations/c1/messages")!;
    expect(ask.headers.get("X-Requested-With")).toBe("nursingbot");
    expect(ask.body).toEqual({ content: "What is hand hygiene?", stream: true });
    expect(screen.getByRole("button", { name: "Helpful" })).toBeInTheDocument();
  });

  it("sends users with a reset password to the change-password page", async () => {
    mockApi([configRoute, userMeRoute({ ...USER, must_change_password: true })]);
    renderApp("/");
    expect(await screen.findByText(/reset by an administrator/)).toBeInTheDocument();
    expect(screen.getByLabelText("Current or temporary password")).toBeInTheDocument();
  });

  it("keeps non-admins out of the admin area", async () => {
    mockApi([
      configRoute,
      userMeRoute(),
      ({ url }) =>
        url.startsWith("/api/v1/conversations?")
          ? json({ conversations: [], next_cursor: null })
          : undefined,
    ]);
    renderApp("/admin");
    expect(await screen.findByText("Admins only")).toBeInTheDocument();
  });
});

describe("sign-in pages", () => {
  it("warns about plain HTTP except on this computer", () => {
    const at = (href: string) => new URL(href) as unknown as Location;
    expect(isInsecureConnection(at("http://192.168.1.20/login"))).toBe(true);
    expect(isInsecureConnection(at("http://localhost:5173/login"))).toBe(false);
    expect(isInsecureConnection(at("https://bot.example.org/login"))).toBe(false);
  });

  it("shows the server's reason when sign-in fails", async () => {
    mockApi([
      configRoute,
      guestMeRoute,
      ({ method, url }) =>
        method === "POST" && url === "/api/v1/auth/login"
          ? json({ detail: "Incorrect e-mail or password." }, 401)
          : undefined,
    ]);
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("E-mail"), "priya@example.org");
    await userEvent.type(screen.getByLabelText("Password"), "wrong password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect e-mail or password.");
  });
});
