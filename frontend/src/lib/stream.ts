/**
 * POST a question and read the answer as Server-Sent Events.
 *
 * EventSource cannot POST, so the body is read from fetch() with a stream
 * reader and fed to the SSE parser. Event order from the API:
 *
 *   start?  mode  citations?  token*  [reset  mode  token*]  end  title?
 *
 * `reset` withdraws everything streamed so far (a grounded attempt failed its
 * checks). `end.final_answer` is always the text to keep.
 */
import { ApiError, toApiError } from "./api";
import { createSseParser } from "./sse";
import type { AnswerMode, Citation, StoredMessage, StreamEnd } from "./types";

export interface StreamHandlers {
  onStart?(data: { conversation_id: string; user_message: StoredMessage }): void;
  onMode?(mode: AnswerMode, label: string): void;
  onCitations?(citations: Citation[]): void;
  onToken?(text: string): void;
  onReset?(): void;
  onEnd?(data: StreamEnd): void;
  onTitle?(data: { conversation_id: string; title: string }): void;
}

export class StreamError extends Error {}

export async function streamAnswer(
  url: string,
  init: RequestInit,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, { ...init, signal, credentials: "same-origin" });
  if (!response.ok) throw await toApiError(response);
  if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) {
    throw new ApiError(response.status, "Unexpected response from the server.");
  }

  let failure: string | null = null;
  const parser = createSseParser((event, raw) => {
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      return; // ignore a malformed event rather than break the whole answer
    }
    switch (event) {
      case "start":
        handlers.onStart?.(data as { conversation_id: string; user_message: StoredMessage });
        break;
      case "mode": {
        const mode = data as { answer_mode: AnswerMode; label: string };
        handlers.onMode?.(mode.answer_mode, mode.label);
        break;
      }
      case "citations":
        handlers.onCitations?.(data as Citation[]);
        break;
      case "token":
        handlers.onToken?.(String(data));
        break;
      case "reset":
        handlers.onReset?.();
        break;
      case "end":
        handlers.onEnd?.(data as StreamEnd);
        break;
      case "title":
        handlers.onTitle?.(data as { conversation_id: string; title: string });
        break;
      case "error":
        failure =
          (data as { detail?: string }).detail ??
          "The assistant is temporarily unavailable. Please try again.";
        break;
    }
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.finish();
  } finally {
    reader.releaseLock();
  }
  if (failure) throw new StreamError(failure);
}
