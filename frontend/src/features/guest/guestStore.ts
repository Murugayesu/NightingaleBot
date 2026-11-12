/**
 * Guest chats live only in this browser, in IndexedDB.
 *
 * If IndexedDB is unavailable (some private-browsing modes block it), chats
 * are kept in memory for the life of the tab instead.
 */
import type { AnswerMode, Citation, MessageStatus, Role } from "../../lib/types";

export interface GuestMessage {
  id: string;
  role: Role;
  content: string;
  answerMode: AnswerMode | null;
  label: string | null;
  citations: Citation[];
  status: MessageStatus;
  createdAt: string;
}

export interface GuestConversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: GuestMessage[];
}

const DB_NAME = "nursingbot-guest";
const STORE = "conversations";
const VERSION = 1;

let dbPromise: Promise<IDBDatabase | null> | null = null;
const memory = new Map<string, GuestConversation>();

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    try {
      const request = indexedDB.open(DB_NAME, VERSION);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE)) {
          request.result.createObjectStore(STORE, { keyPath: "id" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return dbPromise;
}

function run<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        if (!db) {
          reject(new Error("no-indexeddb"));
          return;
        }
        const transaction = db.transaction(STORE, mode);
        const request = action(transaction.objectStore(STORE));
        transaction.oncomplete = () => resolve(request.result);
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      }),
  );
}

async function usingDb(): Promise<boolean> {
  return (await openDb()) !== null;
}

export async function listGuestConversations(): Promise<GuestConversation[]> {
  const all = (await usingDb())
    ? await run<GuestConversation[]>("readonly", (s) => s.getAll())
    : [...memory.values()];
  return all.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export async function getGuestConversation(id: string): Promise<GuestConversation | null> {
  if (!(await usingDb())) return memory.get(id) ?? null;
  return (await run<GuestConversation | undefined>("readonly", (s) => s.get(id))) ?? null;
}

export async function saveGuestConversation(conversation: GuestConversation): Promise<void> {
  if (!(await usingDb())) {
    memory.set(conversation.id, structuredClone(conversation));
    return;
  }
  await run("readwrite", (s) => s.put(conversation));
}

export async function deleteGuestConversation(id: string): Promise<void> {
  if (!(await usingDb())) {
    memory.delete(id);
    return;
  }
  await run("readwrite", (s) => s.delete(id));
}

export async function clearGuestConversations(): Promise<void> {
  if (!(await usingDb())) {
    memory.clear();
    return;
  }
  await run("readwrite", (s) => s.clear());
}

/** Test helper: forget the open connection (fake-indexeddb is reset per test). */
export function resetGuestStoreForTests(): void {
  dbPromise = null;
  memory.clear();
}

export function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/** The shape POST /conversations/import expects. */
export function toImportPayload(conversation: GuestConversation) {
  return {
    title: conversation.title.slice(0, 200),
    created_at: conversation.createdAt,
    messages: conversation.messages
      .filter((m) => m.content.trim() && m.status === "complete")
      .slice(0, 200)
      .map((m) => ({
        role: m.role,
        content: m.content.slice(0, 20_000),
        answer_mode: m.answerMode,
        citations: m.citations.slice(0, 20).map((c) => ({
          source_index: c.source_index,
          title: c.title.slice(0, 512),
          page_number: c.page_number,
          snippet: c.snippet.slice(0, 2000),
          document_id: c.document_id.slice(0, 64),
          chunk_id: c.chunk_id.slice(0, 64),
          score: c.score,
        })),
        created_at: m.createdAt,
      })),
  };
}
