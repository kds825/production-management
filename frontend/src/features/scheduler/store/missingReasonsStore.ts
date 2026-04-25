/**
 * Week 5 Task 5B.2 — "사유 미기록" 카운트 push 스토어.
 *
 * 왜 별도 스토어:
 *   - GET /api/change-sets/missing-reasons 응답 1 정수만 들고 있으면 충분.
 *   - 토스트 칩 클릭 / X / 자동 만료 시 즉시 갱신 트리거 (`refresh()`) 가
 *     필요한데, 토스트 컴포넌트는 zustand store 의 함수 참조만 받고
 *     호출하면 되므로 prop drilling 부담 없음.
 *   - scheduleStore 와 라이프사이클이 다르고 (scheduler 페이지 mount 동안만
 *     의미 있음), 다른 store 로 직접 의존이 없어 응집도 유지에 유리.
 *
 * fetch 실패 시:
 *   백엔드 미배포 / 네트워크 단절 등에선 count 를 0 으로 유지 (배지 미노출).
 *   에러 토스트 출력은 의도적으로 하지 않음 — 배지는 운영자 보조 도구이므로
 *   주된 작업 흐름을 방해하면 안 됨.
 */
import { create } from "zustand";
import { apiFetch } from "@/shared/api/client";

interface MissingReasonsResponse {
  count: number;
  since: string;
}

interface MissingReasonsState {
  count: number;
  loading: boolean;
  refresh: () => Promise<void>;
}

export const useMissingReasonsStore = create<MissingReasonsState>((set) => ({
  count: 0,
  loading: false,
  refresh: async () => {
    set({ loading: true });
    try {
      const data = await apiFetch<MissingReasonsResponse>(
        "/change-sets/missing-reasons",
      );
      set({ count: data.count, loading: false });
    } catch {
      // 네트워크/백엔드 오류 — 배지 비노출 상태(0) 유지.
      set({ loading: false });
    }
  },
}));
