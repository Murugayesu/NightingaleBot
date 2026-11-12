import { afterEach, describe, expect, it, vi } from "vitest";

import { json, sseResponse } from "../test/sse";
import { ApiError } from "./api";
import { StreamError, streamAnswer } from "./stream";

afterEach(() => vi.unstubAllGlobals());

describe("streamAnswer", () => {
  it("dispatches every event in order, across split reads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          ["mode", { answer_mode: "grounded", label: "From official records" }],
          ["citations", [{ source_index: 1, title: "FAQ.pdf" }]],
          ["token", "The fee "],
          ["token", "is Rs. 365 [Source 1]."],
          ["reset", {}],
          ["mode", { answer_mode: "general", label: "General" }],
          ["token", "Hand hygiene matters."],
          [
            "end",
            { final_answer: "Hand hygiene matters.", answer_mode: "general", citations: [] },
          ],
          ["title", { conversation_id: "c1", title: "Hygiene" }],
        ]),
      ),
    );
    const seen: string[] = [];
    await streamAnswer(
      "/api/v1/chat",
      { method: "POST" },
      {
        onMode: (mode) => seen.push(`mode:${mode}`),
        onCitations: (c) => seen.push(`citations:${c.length}`),
        onToken: (text) => seen.push(`token:${text}`),
        onReset: () => seen.push("reset"),
        onEnd: (end) => seen.push(`end:${end.answer_mode}`),
        onTitle: (data) => seen.push(`title:${data.title}`),
      },
    );
    expect(seen).toEqual([
      "mode:grounded",
      "citations:1",
      "token:The fee ",
      "token:is Rs. 365 [Source 1].",
      "reset",
      "mode:general",
      "token:Hand hygiene matters.",
      "end:general",
      "title:Hygiene",
    ]);
  });

  it("raises StreamError for an error event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          ["token", "partial"],
          ["error", { detail: "Provider down" }],
        ]),
      ),
    );
    await expect(streamAnswer("/x", {}, {})).rejects.toThrow(new StreamError("Provider down"));
  });

  it("raises ApiError with the detail of a rejected request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "You have reached the daily usage limit." }), {
            status: 429,
            headers: { "Retry-After": "3600" },
          }),
      ),
    );
    const error = await streamAnswer("/x", {}, {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(429);
    expect((error as ApiError).retryAfter).toBe(3600);
    expect((error as ApiError).message).toMatch(/usage limit/);
  });

  it("rejects a response that is not an event stream", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ answer: "no stream" })));
    await expect(streamAnswer("/x", {}, {})).rejects.toBeInstanceOf(ApiError);
  });
});
