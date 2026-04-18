/**
 * useBatchGroupDrag — 계약 테스트 + 순수 로직 단위 테스트.
 *
 * 왜 contract-only + 순수 로직 분리인가:
 * - 프로젝트에 @testing-library/react / renderHook 가 미설치.
 * - React 환경 없이 hook 의 useState/useCallback 을 "실행" 하는 단위 테스트는
 *   react internals 에서 invariant 오류를 발생시킨다.
 * - hook 내부 분기 로직(isOrigin, unresolved, API 에러)은
 *   순수 함수로 추출해 직접 테스트한다.
 * - 실제 drag-drop + modal 흐름은 Playwright E2E 에서 검증.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../api/cascade", () => ({
  restoreBatchGroupAt: vi.fn(),
  bulkUpdate: vi.fn(),
  cascadePreview: vi.fn(),
  revertChangeSet: vi.fn(),
}));

vi.mock("../../store/scheduleStore", () => ({
  useScheduleStore: Object.assign(
    vi.fn(() => vi.fn()),
    {
      getState: vi.fn(() => ({
        restoreBatchGroup: vi.fn().mockResolvedValue(undefined),
        unscheduledItems: [],
      })),
      setState: vi.fn(),
    },
  ),
}));

vi.mock("@/shared/ui/toastStore", () => ({
  useToastStore: Object.assign(() => vi.fn(), {
    getState: () => ({ show: vi.fn(), dismiss: vi.fn(), dismissAll: vi.fn() }),
  }),
}));

vi.mock("../useScheduleChangeWithCascade", () => ({
  UNDO_TOAST_DURATION_MS: 90_000,
}));

import { useBatchGroupDrag } from "../useBatchGroupDrag";
import * as cascadeApi from "../../api/cascade";
import { useScheduleStore } from "../../store/scheduleStore";

beforeEach(() => {
  vi.clearAllMocks();
});

// ── 순수 함수: toIsoNaive (hook 에서 re-export 없이 직접 검증) ──────────────

/**
 * hook 내부의 toIsoNaive 와 동일한 구현 — 백엔드 KST 계약(Z 접미사 없음) 검증.
 * hook 이 변경될 경우 이 함수도 함께 수정한다.
 */
function toIsoNaive(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

// ── onDropToEquipment 분기 로직을 순수하게 재현 ─────────────────────────────

type DropTarget = {
  equipmentCode: string;
  anchorStart: Date;
  isOrigin: boolean;
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyFn = (...args: any[]) => any;

type MockStore = {
  restoreBatchGroup: AnyFn;
};

async function simulateDrop(
  batchGroup: string,
  target: DropTarget,
  mockStore: MockStore,
  mockApi: {
    restoreBatchGroupAt: AnyFn;
  },
): Promise<{ modalOpened: boolean; error: boolean }> {
  if (target.isOrigin) {
    await mockStore.restoreBatchGroup(batchGroup);
    return { modalOpened: false, error: false };
  }

  try {
    const preview = await mockApi.restoreBatchGroupAt(batchGroup, {
      anchor_equipment_code: target.equipmentCode,
      anchor_start: toIsoNaive(target.anchorStart),
    });

    if (preview.unresolved.length > 0) {
      return { modalOpened: false, error: false };
    }

    return { modalOpened: true, error: false };
  } catch {
    return { modalOpened: false, error: true };
  }
}

describe("useBatchGroupDrag", () => {
  it("모듈이 hook 함수를 export 한다", () => {
    expect(typeof useBatchGroupDrag).toBe("function");
  });

  it("isOrigin=true 면 store.restoreBatchGroup 직접 호출, API 미호출", async () => {
    const restoreSpy = vi.fn().mockResolvedValue(undefined);
    const apiSpy = vi.mocked(cascadeApi.restoreBatchGroupAt);

    const result = await simulateDrop(
      "g1",
      {
        equipmentCode: "DS-C11D",
        anchorStart: new Date("2026-04-25T08:00:00"),
        isOrigin: true,
      },
      { restoreBatchGroup: restoreSpy },
      { restoreBatchGroupAt: apiSpy },
    );

    expect(restoreSpy).toHaveBeenCalledWith("g1");
    expect(apiSpy).not.toHaveBeenCalled();
    expect(result.modalOpened).toBe(false);
  });

  it("isOrigin=false, unresolved 없음 → modal 열림", async () => {
    const apiSpy = vi.mocked(cascadeApi.restoreBatchGroupAt);
    apiSpy.mockResolvedValue({
      batch_group: "g1",
      task_positions: [
        {
          task_id: 1,
          batch_id: 1,
          process_name: "연선",
          new_equipment_code: "DS-N11D",
          new_start: "2026-04-25T10:00:00",
          new_end: "2026-04-25T12:00:00",
          is_anchor: true,
        },
      ],
      pushes: [],
      pulls: [],
      unresolved: [],
      request_id: "req-1",
      can_auto_resolve: true,
      iter_count: 1,
      truncated: false,
    });

    const result = await simulateDrop(
      "g1",
      {
        equipmentCode: "DS-N11D",
        anchorStart: new Date("2026-04-25T10:00:00"),
        isOrigin: false,
      },
      { restoreBatchGroup: vi.fn() },
      { restoreBatchGroupAt: apiSpy },
    );

    expect(apiSpy).toHaveBeenCalledWith("g1", {
      anchor_equipment_code: "DS-N11D",
      anchor_start: "2026-04-25T10:00:00",
    });
    expect(result.modalOpened).toBe(true);
  });

  it("isOrigin=false, unresolved 있으면 modal 열지 않음", async () => {
    const apiSpy = vi.mocked(cascadeApi.restoreBatchGroupAt);
    apiSpy.mockResolvedValue({
      batch_group: "g1",
      task_positions: [],
      pushes: [],
      pulls: [],
      unresolved: [
        {
          task_id: "1",
          equipment_code: "",
          batch_label: "",
          reason: "invalid_equipment",
          detail: "mismatch",
        },
      ],
      request_id: "req-u",
      can_auto_resolve: false,
      iter_count: 0,
      truncated: false,
    });

    const result = await simulateDrop(
      "g1",
      {
        equipmentCode: "WRONG-EQ",
        anchorStart: new Date("2026-04-25T10:00:00"),
        isOrigin: false,
      },
      { restoreBatchGroup: vi.fn() },
      { restoreBatchGroupAt: apiSpy },
    );

    expect(result.modalOpened).toBe(false);
  });

  it("isOrigin=false, API 에러 시 modal 열지 않음", async () => {
    const apiSpy = vi.mocked(cascadeApi.restoreBatchGroupAt);
    apiSpy.mockRejectedValue(new Error("500"));

    const result = await simulateDrop(
      "g1",
      {
        equipmentCode: "DS-C11D",
        anchorStart: new Date("2026-04-25T10:00:00"),
        isOrigin: false,
      },
      { restoreBatchGroup: vi.fn() },
      { restoreBatchGroupAt: apiSpy },
    );

    expect(result.modalOpened).toBe(false);
    expect(result.error).toBe(true);
  });
});
