import { describe, it, expect, vi, beforeEach } from "vitest";
import { cascadePreview, bulkUpdate, revertChangeSet } from "../cascade";
import { BulkUpdateError } from "../cascade.types";

/**
 * Cascade API 클라이언트 단위 테스트.
 *
 * fetch 를 global mock 으로 교체해 HTTP 레이어만 격리 검증.
 * URL 경로 / 헤더 / 에러 변환 계약을 모두 확인.
 */

type FetchMock = ReturnType<typeof vi.fn>;

describe("cascade API", () => {
  beforeEach(() => {
    // fetch 는 테스트마다 새 mock — 이전 호출 내역이 섞이지 않도록.
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  it("cascadePreview sends X-Cascade-API-Version: 2 header", async () => {
    (global.fetch as unknown as FetchMock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        request_id: "r1",
        summary: "",
        pushes: [],
        pulls: [],
        unresolved: [],
        can_auto_resolve: true,
        iter_count: 0,
        truncated: false,
      }),
    });

    await cascadePreview({
      task_id: "X",
      new_start: "2026-04-20T10:00:00",
      new_end: "2026-04-20T12:00:00",
    });

    const call = (global.fetch as unknown as FetchMock).mock.calls[0];
    // call[0] 은 URL, call[1] 은 init. headers 는 Record<string,string> 로 직접 조회 가능.
    const init = call[1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Cascade-API-Version"]).toBe("2");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(String(call[0])).toContain("/schedules/cascade-preview");
  });

  it("cascadePreview throws on non-ok", async () => {
    (global.fetch as unknown as FetchMock).mockResolvedValueOnce({
      ok: false,
      status: 500,
    });
    await expect(
      cascadePreview({
        task_id: "X",
        new_start: "2026-04-20T10:00:00",
        new_end: "2026-04-20T12:00:00",
      }),
    ).rejects.toThrow(/cascade-preview 500/);
  });

  it("bulkUpdate throws BulkUpdateError on 422 with detail wrapper", async () => {
    // FastAPI HTTPException(detail={...}) 포맷.
    (global.fetch as unknown as FetchMock).mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({
        detail: {
          error_code: "VALIDATION_OVERLAP_SAME_EQUIPMENT",
          offending_task_id: "A",
          detail: "",
          can_retry: true,
        },
      }),
    });
    try {
      await bulkUpdate({ changes: [] });
      expect.fail("should have thrown BulkUpdateError");
    } catch (e) {
      expect(e).toBeInstanceOf(BulkUpdateError);
      const err = e as BulkUpdateError;
      expect(err.code).toBe("VALIDATION_OVERLAP_SAME_EQUIPMENT");
      expect(err.offendingTaskId).toBe("A");
      expect(err.canRetry).toBe(true);
    }
  });

  it("bulkUpdate throws BulkUpdateError when detail is root-level", async () => {
    // detail 래핑 없이 root 에 payload 가 오는 케이스도 지원 (방어적 파싱).
    (global.fetch as unknown as FetchMock).mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({
        error_code: "FEATURE_DISABLED",
        offending_task_id: "X",
        detail: "disabled",
        can_retry: false,
      }),
    });
    await expect(bulkUpdate({ changes: [] })).rejects.toBeInstanceOf(
      BulkUpdateError,
    );
  });

  it("revertChangeSet throws on 404", async () => {
    (global.fetch as unknown as FetchMock).mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () => "not found",
    });
    await expect(revertChangeSet("x")).rejects.toThrow(/revert 404/);
  });

  it("revertChangeSet returns reverted:true on success", async () => {
    (global.fetch as unknown as FetchMock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ reverted: true, change_set_id: "cs-1" }),
    });
    const r = await revertChangeSet("cs-1");
    expect(r.reverted).toBe(true);
  });
});
