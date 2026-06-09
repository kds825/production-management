"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/** 파이프라인 실행 레코드 (GET /api/pipeline/runs) */
export interface PipelineRun {
  run_label: string;
  created_at?: string;
  status?: string;
  warning_count?: number;
  batch_count?: number;
  outsource_count?: number;
  /** stage1/update 로 파생된 경우 이전 run_label — 두 버전 비교의 기본 before 값 */
  parent_run_label?: string | null;
}

/**
 * 파이프라인 런 목록 로드 + 선택 결정 훅.
 *
 * 우선순위:
 *  1. URL 쿼리(`run_label=...`) 가 있고 응답에 살아 있으면 그걸로
 *  2. 이미 선택돼 있고 목록에 살아 있으면 유지
 *  3. 응답 첫 번째 (최신)
 *
 * 외부 시그니처는 page.tsx 가 사용하던 것과 동일 — runs/selectedRun/
 * runsLoading/setSelectedRun/loadRuns/selectedRunInfo 를 그대로 노출.
 */
export function useRunsList(urlRunLabel: string | null) {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<string>("");
  const [runsLoading, setRunsLoading] = useState(false);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/pipeline/runs`);
      if (res.ok) {
        const data: PipelineRun[] = await res.json();
        setRuns(data);
        if (data.length > 0) {
          setSelectedRun((prev) => {
            // 1순위: URL 쿼리에 run_label 이 있고 응답 목록에도 있으면 그걸로.
            if (urlRunLabel && data.some((r) => r.run_label === urlRunLabel)) {
              return urlRunLabel;
            }
            // 2순위: 이미 선택돼 있고 목록에 살아 있으면 유지.
            if (prev && data.some((r) => r.run_label === prev)) {
              return prev;
            }
            // 3순위: 최신 (응답 첫 번째).
            return data[0].run_label;
          });
        }
      }
    } catch {
      // 연결 실패 시 조용히 처리 — 기존 기능에 영향 없음
    } finally {
      setRunsLoading(false);
    }
  }, [urlRunLabel]);

  // 런 목록 초기 로드
  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  // 선택된 런의 요약 정보
  const selectedRunInfo = useMemo(
    () => runs.find((r) => r.run_label === selectedRun),
    [runs, selectedRun],
  );

  return {
    runs,
    selectedRun,
    setSelectedRun,
    runsLoading,
    loadRuns,
    selectedRunInfo,
  };
}
