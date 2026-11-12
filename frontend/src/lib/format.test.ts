import { describe, expect, it } from "vitest";

import { dateGroup, titleFrom } from "./format";

describe("dateGroup", () => {
  const now = new Date(2026, 10, 12, 15, 0, 0);
  it("buckets by calendar day", () => {
    expect(dateGroup(new Date(2026, 10, 12, 1).toISOString(), now)).toBe("today");
    expect(dateGroup(new Date(2026, 10, 10).toISOString(), now)).toBe("week");
    expect(dateGroup(new Date(2026, 9, 20).toISOString(), now)).toBe("month");
    expect(dateGroup(new Date(2025, 1, 1).toISOString(), now)).toBe("older");
  });
});

describe("titleFrom", () => {
  it("keeps short questions and trims long ones at a word", () => {
    expect(titleFrom("  What is the   fee? ")).toBe("What is the fee?");
    const long = titleFrom(
      "How do I renew my nursing registration with the council after it has lapsed for two years?",
    );
    expect(long.length).toBeLessThanOrEqual(60);
    expect(long.endsWith("…")).toBe(true);
    expect(titleFrom("")).toBe("New chat");
  });
});
