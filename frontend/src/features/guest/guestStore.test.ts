import { IDBFactory } from "fake-indexeddb";
import { beforeEach, describe, expect, it } from "vitest";

import {
  type GuestConversation,
  clearGuestConversations,
  deleteGuestConversation,
  getGuestConversation,
  listGuestConversations,
  resetGuestStoreForTests,
  saveGuestConversation,
  toImportPayload,
} from "./guestStore";

function conversation(id: string, updatedAt: string): GuestConversation {
  return {
    id,
    title: `Chat ${id}`,
    createdAt: updatedAt,
    updatedAt,
    messages: [
      {
        id: `${id}-q`,
        role: "user",
        content: "What is the fee?",
        answerMode: null,
        label: null,
        citations: [],
        status: "complete",
        createdAt: updatedAt,
      },
      {
        id: `${id}-a`,
        role: "assistant",
        content: "Rs. 365 [Source 1].",
        answerMode: "grounded",
        label: "From official records",
        citations: [
          {
            source_index: 1,
            chunk_id: "c",
            document_id: "d",
            title: "FAQ.pdf",
            page_number: 1,
            score: 0.6,
            snippet: "fee",
          },
        ],
        status: "complete",
        createdAt: updatedAt,
      },
      {
        id: `${id}-s`,
        role: "assistant",
        content: "partial",
        answerMode: null,
        label: null,
        citations: [],
        status: "stopped",
        createdAt: updatedAt,
      },
    ],
  };
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  resetGuestStoreForTests();
});

describe("guest store", () => {
  it("saves, lists newest first, reads and deletes", async () => {
    await saveGuestConversation(conversation("a", "2026-11-10T10:00:00Z"));
    await saveGuestConversation(conversation("b", "2026-11-11T10:00:00Z"));
    expect((await listGuestConversations()).map((c) => c.id)).toEqual(["b", "a"]);
    expect((await getGuestConversation("a"))?.title).toBe("Chat a");
    await deleteGuestConversation("a");
    expect(await getGuestConversation("a")).toBeNull();
    await clearGuestConversations();
    expect(await listGuestConversations()).toEqual([]);
  });

  it("builds an import payload of complete messages only", () => {
    const payload = toImportPayload(conversation("a", "2026-11-10T10:00:00Z"));
    expect(payload.messages).toHaveLength(2);
    expect(payload.messages[1]).toMatchObject({ role: "assistant", answer_mode: "grounded" });
    expect(payload.messages[1]!.citations[0]!.title).toBe("FAQ.pdf");
  });
});
