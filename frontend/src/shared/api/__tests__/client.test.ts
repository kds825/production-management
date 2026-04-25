import { describe, it, expect, afterEach, vi } from "vitest";
import { apiFetch, ApiError } from "../client";

/**
 * Week 4 Task 4B.4 — apiFetch 가 X-Run-Id 헤더를 ApiError 에 부착하는지 검증.
 *
 * 백엔드 Week 2 Task 2A.4 미들웨어가 모든 응답에 발급하므로, 운영자가 토스트에서
 * 에러 코드를 복사해 담당자에게 전달하는 흐름의 첫 단계가 이 부착이다.
 *
 * 환경: vitest environment="node" — Headers / fetch 는 Node 18+ 빌트인.
 * package.json 의 fetch / Headers 폴리필이 별도로 필요 없다.
 */

const TEST_BASE = "http://localhost:8000/api";

describe("apiFetch — ApiError runId 회수", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("500 응답의 X-Run-Id 헤더를 ApiError.runId 로 부착한다", async () => {
    const runId = "11111111-2222-3333-4444-555555555555";
    const mockFetch = vi.fn(
      async () =>
        // Response 도 Node 18+ 빌트인. 헤더는 Headers spec 상 case-insensitive lookup.
        new Response(null, {
          status: 500,
          statusText: "Internal Server Error",
          headers: { "X-Run-Id": runId },
        }),
    );
    vi.stubGlobal("fetch", mockFetch);

    let caught: unknown = null;
    try {
      await apiFetch(`/test`);
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const err = caught as ApiError;
    expect(err.runId).toBe(runId);
    expect(err.status).toBe(500);
    expect(err.statusText).toBe("Internal Server Error");
    expect(mockFetch).toHaveBeenCalledWith(
      `${TEST_BASE}/test`,
      expect.objectContaining({
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("Headers.get 은 case-insensitive — 'x-run-id' (소문자) 도 회수한다", async () => {
    // 일부 프록시는 헤더 이름을 정규화한다. fetch 스펙상 Headers.get 은 case-insensitive 이므로
    // 동작이 동일해야 한다 — 회귀 방지.
    const runId = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(null, {
            status: 422,
            statusText: "Unprocessable Entity",
            headers: { "x-run-id": runId },
          }),
      ),
    );

    let caught: unknown = null;
    try {
      await apiFetch(`/foo`);
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).runId).toBe(runId);
  });

  it("X-Run-Id 헤더가 없으면 runId === null (graceful)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(null, {
            status: 502,
            statusText: "Bad Gateway",
            // 헤더 없음 — 네트워크 단절·CORS preflight 실패 등 극단 케이스 시뮬레이션.
          }),
      ),
    );

    let caught: unknown = null;
    try {
      await apiFetch(`/bar`);
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).runId).toBeNull();
    expect((caught as ApiError).status).toBe(502);
  });

  it("2xx 응답은 ApiError 를 던지지 않고 JSON 을 반환한다 (회귀 방지)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ ok: true }), {
            status: 200,
            headers: {
              "Content-Type": "application/json",
              "X-Run-Id": "ignored-on-success",
            },
          }),
      ),
    );

    const data = await apiFetch<{ ok: boolean }>(`/baz`);
    expect(data).toEqual({ ok: true });
  });
});

describe("ApiError — message 포맷 backward compatibility", () => {
  it("message 는 'API Error: {status} {statusText}' 포맷 (기존 호출처가 e.message 그대로 토스트에 표시)", () => {
    const err = new ApiError(500, "Internal Server Error", null);
    expect(err.message).toBe("API Error: 500 Internal Server Error");
    expect(err.name).toBe("ApiError");
  });
});
