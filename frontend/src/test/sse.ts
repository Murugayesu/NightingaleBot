/** Helpers to fake the API in component tests. */
export function sseBody(events: Array<[string, unknown]>): string {
  return events
    .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}

/** A Response whose body arrives in small pieces, like a real network stream. */
export function sseResponse(events: Array<[string, unknown]>, pieceSize = 7): Response {
  const text = sseBody(events);
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < text.length; i += pieceSize) {
        controller.enqueue(encoder.encode(text.slice(i, i + pieceSize)));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

export function json(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
