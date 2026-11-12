import { afterEach, describe, expect, it, vi } from "vitest";

import { json } from "../test/sse";
import { ApiError, api, request } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("request", () => {
  it("adds the CSRF header to state-changing requests only", async () => {
    const fetchMock = vi.fn(async () => json({}));
    vi.stubGlobal("fetch", fetchMock);
    await request("/conversations");
    await api.createConversation("Fees");
    const [getCall, postCall] = fetchMock.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(new Headers(getCall![1].headers).has("X-Requested-With")).toBe(false);
    const postHeaders = new Headers(postCall![1].headers);
    expect(postHeaders.get("X-Requested-With")).toBe("nursingbot");
    expect(postHeaders.get("Content-Type")).toBe("application/json");
    expect(postCall![0]).toBe("/api/v1/conversations");
    expect(postCall![1].credentials).toBe("same-origin");
  });

  it("turns FastAPI error shapes into readable messages", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json({ detail: "Incorrect e-mail or password." }, 401))
        .mockResolvedValueOnce(
          json({ detail: [{ msg: "Field required" }, { msg: "Too long" }] }, 422),
        )
        .mockResolvedValueOnce(new Response("<html>Bad gateway</html>", { status: 502 })),
    );
    await expect(api.login({ email: "a", password: "b" })).rejects.toMatchObject({
      status: 401,
      message: "Incorrect e-mail or password.",
    });
    await expect(api.me()).rejects.toMatchObject({ message: "Field required Too long" });
    const error = await api.me().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toMatch(/temporarily unavailable/);
  });

  it("returns undefined for 204 responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(api.logout()).resolves.toBeUndefined();
  });
});
