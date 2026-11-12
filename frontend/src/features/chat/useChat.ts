/**
 * One conversation's messages and the actions on them, for both kinds of user:
 *
 *   signed in  messages live on the server; questions go to
 *              POST /conversations/{id}/messages (history is loaded there)
 *   guest      messages live in IndexedDB; questions go to POST /chat with
 *              the last few turns as recent_context
 *
 * While an answer streams, a local working copy (`live`) is shown. When the
 * stream ends it is written back (server refetch or IndexedDB save) and the
 * working copy is dropped.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { keys } from "../../app/queries";
import { API_BASE, ApiError, CSRF_HEADER, api } from "../../lib/api";
import { t } from "../../lib/strings";
import { StreamError, type StreamHandlers, streamAnswer } from "../../lib/stream";
import type {
  AnswerMode,
  AppConfig,
  Citation,
  MessageStatus,
  Role,
  StoredMessage,
} from "../../lib/types";
import { titleFrom } from "../../lib/format";
import {
  type GuestConversation,
  type GuestMessage,
  getGuestConversation,
  newId,
  saveGuestConversation,
} from "../guest/guestStore";
import { forgetGuestToken, guestToken } from "../guest/guestSession";

export interface ChatMessage {
  key: string;
  /** Server message id (signed in) or local id (guest); null until known. */
  id: string | null;
  role: Role;
  content: string;
  answerMode: AnswerMode | null;
  label: string | null;
  citations: Citation[];
  status: MessageStatus | "streaming";
  isImported: boolean;
  feedback: 1 | -1 | null;
}

export type Phase = "idle" | "searching" | "writing";

export interface ChatController {
  messages: ChatMessage[];
  title: string | null;
  loading: boolean;
  notFound: boolean;
  phase: Phase;
  busy: boolean;
  error: string | null;
  clearError(): void;
  /** Resolves false when the question was not accepted (restore the input). */
  send(text: string): Promise<boolean>;
  stop(): void;
  regenerate(): Promise<void>;
  rate(message: ChatMessage, rating: 1 | -1 | null, comment?: string): Promise<void>;
}

export function fromStored(m: StoredMessage): ChatMessage {
  return {
    key: m.id,
    id: m.id,
    role: m.role,
    content: m.content,
    answerMode: m.answer_mode,
    label: m.label,
    citations: m.citations,
    status: m.status,
    isImported: m.is_imported,
    feedback: m.feedback,
  };
}

function fromGuest(m: GuestMessage): ChatMessage {
  return {
    key: m.id,
    id: m.id,
    role: m.role,
    content: m.content,
    answerMode: m.answerMode,
    label: m.label,
    citations: m.citations,
    status: m.status,
    isImported: false,
    feedback: null,
  };
}

function toGuest(m: ChatMessage): GuestMessage {
  return {
    id: m.id ?? m.key,
    role: m.role,
    content: m.content,
    answerMode: m.answerMode,
    label: m.label,
    citations: m.citations,
    status: m.status === "streaming" ? "stopped" : m.status,
    createdAt: new Date().toISOString(),
  };
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function draftMessage(): ChatMessage {
  return {
    key: newId(),
    id: null,
    role: "assistant",
    content: "",
    answerMode: null,
    label: null,
    citations: [],
    status: "streaming",
    isImported: false,
    feedback: null,
  };
}

export function useChat(
  conversationId: string,
  isGuest: boolean,
  config: AppConfig | undefined,
): ChatController {
  const client = useQueryClient();
  const [live, setLive] = useState<ChatMessage[] | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const liveRef = useRef<ChatMessage[] | null>(null);

  const serverQuery = useQuery({
    queryKey: keys.conversation(conversationId),
    queryFn: () => api.getConversation(conversationId),
    enabled: !isGuest,
    retry: (count, e) => count < 2 && !(e instanceof ApiError && e.status < 500),
  });
  const guestQuery = useQuery({
    queryKey: keys.guestConversation(conversationId),
    queryFn: () => getGuestConversation(conversationId),
    enabled: isGuest,
  });

  // Abort a running answer when leaving the conversation. Deferred one tick:
  // React's StrictMode simulates an unmount and immediately remounts, which
  // must not cancel the first answer.
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      window.setTimeout(() => {
        if (!mounted.current) controllerRef.current?.abort();
      }, 0);
    };
  }, []);

  const stored: ChatMessage[] = isGuest
    ? (guestQuery.data?.messages ?? []).map(fromGuest)
    : (serverQuery.data?.messages ?? []).map(fromStored);
  const messages = live ?? stored;

  // The ref is the source of truth and changes synchronously; React state
  // follows it. (A setState updater runs later, during render, so saving at
  // the end of a stream could otherwise read a stale list.)
  const update = useCallback((change: (messages: ChatMessage[]) => ChatMessage[]) => {
    const next = change(liveRef.current ?? []);
    liveRef.current = next;
    setLive(next);
  }, []);

  const patchDraft = useCallback(
    (draftKey: string, patch: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>)) =>
      update((list) =>
        list.map((m) =>
          m.key === draftKey ? { ...m, ...(typeof patch === "function" ? patch(m) : patch) } : m,
        ),
      ),
    [update],
  );

  const handlersFor = useCallback(
    (draftKey: string, questionKey: string | null): StreamHandlers => ({
      onStart: (data) => {
        if (questionKey) {
          update((list) =>
            list.map((m) => (m.key === questionKey ? { ...m, id: data.user_message.id } : m)),
          );
        }
      },
      onMode: (answerMode, label) => patchDraft(draftKey, { answerMode, label }),
      onCitations: (citations) => patchDraft(draftKey, { citations }),
      onToken: (text) => {
        setPhase("writing");
        patchDraft(draftKey, (m) => ({ content: m.content + text }));
      },
      onReset: () => patchDraft(draftKey, { content: "", citations: [] }),
      onEnd: (end) =>
        patchDraft(draftKey, {
          id: end.message?.id ?? null,
          content: end.final_answer,
          answerMode: end.answer_mode,
          label: end.label,
          citations: end.citations,
          status: "complete",
        }),
      onTitle: () => {
        void client.invalidateQueries({ queryKey: keys.allConversations });
      },
    }),
    [client, patchDraft, update],
  );

  /** Persist the finished exchange and drop the working copy. */
  const settle = useCallback(async () => {
    if (isGuest) {
      const finished = liveRef.current ?? [];
      const existing = await getGuestConversation(conversationId);
      const now = new Date().toISOString();
      const firstQuestion = finished.find((m) => m.role === "user")?.content ?? "";
      const conversation: GuestConversation = {
        id: conversationId,
        title:
          existing?.title && existing.title !== "New chat"
            ? existing.title
            : titleFrom(firstQuestion),
        createdAt: existing?.createdAt ?? now,
        updatedAt: now,
        messages: finished
          .filter((m) => m.content.trim() || m.role === "user")
          .map(toGuest),
      };
      await saveGuestConversation(conversation);
      client.setQueryData(keys.guestConversation(conversationId), conversation);
      await client.invalidateQueries({ queryKey: keys.guestConversations });
    } else {
      await client.refetchQueries({ queryKey: keys.conversation(conversationId) });
      void client.invalidateQueries({ queryKey: keys.allConversations });
    }
    liveRef.current = null;
    setLive(null);
  }, [client, conversationId, isGuest]);

  const runStream = useCallback(
    async (
      base: ChatMessage[],
      question: ChatMessage | null,
      start: (signal: AbortSignal, handlers: StreamHandlers) => Promise<void>,
    ): Promise<boolean> => {
      const draft = draftMessage();
      const initial = question ? [...base, question, draft] : [...base, draft];
      liveRef.current = initial;
      setLive(initial);
      setError(null);
      setPhase("searching");
      const controller = new AbortController();
      controllerRef.current = controller;
      let accepted = true;
      try {
        await start(controller.signal, handlersFor(draft.key, question?.key ?? null));
        patchDraft(draft.key, (m) => (m.status === "streaming" ? { status: "complete" } : {}));
      } catch (caught) {
        if (isAbort(caught)) {
          patchDraft(draft.key, { status: "stopped" });
        } else if (caught instanceof StreamError) {
          setError(caught.message);
          patchDraft(draft.key, { status: "error" });
        } else {
          // Rejected before anything was answered: take the question back.
          accepted = false;
          setError(caught instanceof ApiError ? caught.message : t.common.somethingWrong);
          update(() => base);
        }
      } finally {
        controllerRef.current = null;
        setPhase("idle");
      }
      if (accepted) {
        await settle();
      } else {
        liveRef.current = null;
        setLive(null);
      }
      return accepted;
    },
    [handlersFor, patchDraft, settle, update],
  );

  const guestStream = useCallback(
    (query: string, history: ChatMessage[]) =>
      async (signal: AbortSignal, handlers: StreamHandlers) => {
        const turns = Math.max(0, config?.max_context_turns ?? 2) * 2;
        const recent = history
          .filter((m) => m.status === "complete" && m.content.trim())
          .slice(turns ? -turns : history.length)
          .map((m) => ({ role: m.role, content: m.content.slice(0, 2000) }));
        const post = async () =>
          streamAnswer(
            `${API_BASE}/chat`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-Session-Token": await guestToken(),
              },
              body: JSON.stringify({ query, stream: true, recent_context: recent }),
            },
            handlers,
            signal,
          );
        try {
          await post();
        } catch (caught) {
          if (caught instanceof ApiError && caught.status === 401) {
            forgetGuestToken(); // expired: start a new guest session once
            await post();
            return;
          }
          if (caught instanceof ApiError && caught.status === 429) {
            forgetGuestToken();
            if (/session/i.test(caught.message)) throw new ApiError(429, t.chat.guestLimit);
          }
          throw caught;
        }
      },
    [config?.max_context_turns],
  );

  const send = useCallback(
    async (text: string): Promise<boolean> => {
      const content = text.trim();
      if (!content || controllerRef.current) return false;
      const base = (liveRef.current ?? messages).filter((m) => m.status !== "streaming");
      const question: ChatMessage = {
        ...draftMessage(),
        key: newId(),
        id: isGuest ? newId() : null,
        role: "user",
        content,
        status: "complete",
      };
      if (isGuest) return runStream(base, question, guestStream(content, base));
      return runStream(base, question, (signal, handlers) =>
        streamAnswer(
          `${API_BASE}/conversations/${conversationId}/messages`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json", ...CSRF_HEADER },
            body: JSON.stringify({ content, stream: true }),
          },
          handlers,
          signal,
        ),
      );
    },
    [conversationId, guestStream, isGuest, messages, runStream],
  );

  const regenerate = useCallback(async () => {
    if (controllerRef.current) return;
    const list = (liveRef.current ?? messages).filter((m) => m.status !== "streaming");
    const last = list[list.length - 1];
    if (!last) return;
    const base = last.role === "assistant" ? list.slice(0, -1) : list;
    if (isGuest) {
      const question = [...base].reverse().find((m) => m.role === "user");
      if (!question) return;
      const history = base.slice(0, base.lastIndexOf(question));
      await runStream(base, null, guestStream(question.content, history));
      return;
    }
    if (!last.id) return;
    await runStream(base, null, (signal, handlers) =>
      streamAnswer(
        `${API_BASE}/messages/${last.id}/regenerate?stream=true`,
        { method: "POST", headers: { ...CSRF_HEADER } },
        handlers,
        signal,
      ),
    );
  }, [guestStream, isGuest, messages, runStream]);

  const stop = useCallback(() => controllerRef.current?.abort(), []);

  const rate = useCallback(
    async (message: ChatMessage, rating: 1 | -1 | null, comment?: string) => {
      if (isGuest || !message.id) return;
      await api.sendFeedback(message.id, rating, comment);
      client.setQueryData(
        keys.conversation(conversationId),
        (old: Awaited<ReturnType<typeof api.getConversation>> | undefined) =>
          old && {
            ...old,
            messages: old.messages.map((m) =>
              m.id === message.id ? { ...m, feedback: rating } : m,
            ),
          },
      );
    },
    [client, conversationId, isGuest],
  );

  const notFound = isGuest
    ? guestQuery.isSuccess && guestQuery.data === null
    : serverQuery.error instanceof ApiError && serverQuery.error.status === 404;

  return {
    messages,
    title: isGuest ? (guestQuery.data?.title ?? null) : (serverQuery.data?.title ?? null),
    loading: isGuest ? guestQuery.isLoading : serverQuery.isLoading,
    notFound,
    phase,
    busy: phase !== "idle",
    error,
    clearError: () => setError(null),
    send,
    stop,
    regenerate,
    rate,
  };
}
