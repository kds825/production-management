/**
 * useBatchCompareMode — Gantt 버전 비교 모드의 페이지 레벨 오케스트레이션.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - "비교 모드" 토글 — 최신 run / parent_run_label 조회 후 store.enableCompareMode 호출.
 *   - "목록" 모달 가시성 (compareOpen) — store 의 diffResponse 를 재사용해 추가 fetch 없음.
 *   - 에러/경고는 useToastStore 로 안내.
 *
 * 비고:
 *   - compareMode 의 핵심 상태(enabled/loading/error/diffResponse/filters) 는 store 의 DiffSlice
 *     가 단일 진실원이다. 이 hook 은 상위 모달 가시성과 토글 진입 흐름만 관리.
 *   - ESC 닫힘은 useSchedulerKeyboard 가 별도 처리 — 키보드/UI 책임 분리.
 */
"use client";

import { useCallback, useState } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import { useToastStore } from "@/shared/ui/toastStore";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export interface UseBatchCompareModeResult {
  /** "목록" 버튼으로 여는 텍스트 기반 상세 모달 가시성 */
  compareOpen: boolean;
  setCompareOpen: (open: boolean) => void;
  /** [비교 모드] 토글 — 활성 시 close, 비활성 시 최신 run / parent 조회 후 enable */
  handleToggleCompareMode: () => Promise<void>;
}

export function useBatchCompareMode(): UseBatchCompareModeResult {
  const [compareOpen, setCompareOpen] = useState(false);

  const compareEnabled = useScheduleStore((s) => s.compareMode.enabled);
  const enableCompareMode = useScheduleStore((s) => s.enableCompareMode);
  const closeCompareMode = useScheduleStore((s) => s.closeCompareMode);

  // 토글 — 이미 ON 이면 닫고, OFF 면 최신 run/parent 조회 후 enableCompareMode.
  // 에러는 store 가 compareMode.error 로 노출하므로 여기서는 toast 만 띄운다.
  const handleToggleCompareMode = useCallback(async () => {
    if (compareEnabled) {
      closeCompareMode();
      return;
    }
    try {
      const runsRes = await fetch(`${API_BASE}/pipeline/runs`);
      if (!runsRes.ok) {
        useToastStore.getState().show("런 목록 조회 실패", "error");
        return;
      }
      const runs: Array<{
        run_label: string;
        parent_run_label?: string | null;
      }> = await runsRes.json();
      if (runs.length === 0) {
        useToastStore.getState().show("비교할 런이 없습니다.", "warning");
        return;
      }
      const latest = runs[0];
      if (!latest.parent_run_label) {
        useToastStore
          .getState()
          .show("최초 실행 — 비교할 이전 버전이 없습니다.", "warning");
        return;
      }
      await enableCompareMode(latest.parent_run_label, latest.run_label);
    } catch (err) {
      useToastStore
        .getState()
        .show(err instanceof Error ? err.message : String(err), "error");
    }
  }, [compareEnabled, closeCompareMode, enableCompareMode]);

  return { compareOpen, setCompareOpen, handleToggleCompareMode };
}
