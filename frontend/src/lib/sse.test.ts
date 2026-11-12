import { describe, expect, it } from "vitest";

import { createSseParser } from "./sse";

function collect(chunks: string[]) {
  const events: Array<[string, string]> = [];
  const parser = createSseParser((event, data) => events.push([event, data]));
  chunks.forEach((chunk) => parser.push(chunk));
  parser.finish();
  return events;
}

describe("createSseParser", () => {
  it("parses whole events", () => {
    expect(collect(['event: token\ndata: "Hi"\n\nevent: end\ndata: {}\n\n'])).toEqual([
      ["token", '"Hi"'],
      ["end", "{}"],
    ]);
  });

  it("handles events split anywhere, including inside \r\n", () => {
    const body = 'event: token\r\ndata: "a"\r\n\r\nevent: end\r\ndata: {"x":1}\r\n\r\n';
    for (let cut = 1; cut < body.length; cut++) {
      expect(collect([body.slice(0, cut), body.slice(cut)])).toEqual([
        ["token", '"a"'],
        ["end", '{"x":1}'],
      ]);
    }
  });

  it("joins multi-line data, ignores comments, defaults the event name", () => {
    expect(collect([": ping\n", "data: line 1\ndata: line 2\n\n"])).toEqual([
      ["message", "line 1\nline 2"],
    ]);
  });

  it("flushes a final event without a trailing blank line", () => {
    expect(collect(["event: end\ndata: {}"])).toEqual([["end", "{}"]]);
  });
});
