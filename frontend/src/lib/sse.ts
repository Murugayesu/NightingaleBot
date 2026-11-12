/**
 * Incremental Server-Sent Events parser (ported from the 1.0 widget, where it
 * was tested against split reads).
 *
 * Feed decoded text with push() as it arrives; onEvent(name, data) fires for
 * every complete event. Network reads can split an event, or even a "\r\n"
 * pair, anywhere, so an unfinished line stays buffered until the rest
 * arrives. Lines may end in "\r\n", "\n" or "\r". Call finish() at the end of
 * the stream to flush a final event that had no trailing blank line.
 */
export interface SseParser {
  push(text: string): void;
  finish(): void;
}

export function createSseParser(
  onEvent: (event: string, data: string) => void,
): SseParser {
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];

  function dispatch(): void {
    if (dataLines.length > 0) onEvent(eventName || "message", dataLines.join("\n"));
    eventName = "";
    dataLines = [];
  }

  function processLine(line: string): void {
    if (line === "") {
      dispatch(); // a blank line ends the event
      return;
    }
    if (line.startsWith(":")) return; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
    // "id" and "retry" are not used here.
  }

  return {
    push(text: string) {
      let pending = buffer + text;
      // A trailing "\r" may be the first half of a "\r\n" split across reads.
      const heldCr = pending.endsWith("\r");
      if (heldCr) pending = pending.slice(0, -1);
      const lines = pending.split(/\r\n|\r|\n/);
      buffer = (lines.pop() ?? "") + (heldCr ? "\r" : "");
      lines.forEach(processLine);
    },
    finish() {
      const rest = buffer.replace(/\r$/, "");
      buffer = "";
      if (rest !== "") processLine(rest);
      dispatch();
    },
  };
}
