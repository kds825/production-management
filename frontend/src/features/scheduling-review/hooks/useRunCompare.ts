"use client";

import { useCallback, useState } from "react";
import type { PipelineRun } from "@/features/scheduling-review/hooks/useRunsList";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/** GET /api/pipeline/runs/compare 응답 (ScheduleDiffResponse 호환) */
export interface RunCompareResponse {
  run_label_before: string;
  run_label_after: string;
  kind: string;
  created_at: string;
  summary: {
    moved: number;
    added: number;
    removed: number;
    unchanged: number;
    total_before: number;
    total_after: number;
  };
  moved_tasks: Array<{
    task_id: string;
    old_start: string | null;
    old_end: string | null;
    old_equipment: string | null;
    new_start: string | null;
    new_end: string | null;
    new_equipment: string | null;
    start_delta_hours: number | null;
    end_delta_hours: number | null;
    equipment_changed: boolean;
    batch_group?: string | null;
    process_name?: string | null;
    sales_order_id?: string | null;
    customer_name?: string | null;
    sheath_color?: string | null;
    cross_section?: number | null;
  }>;
  added_tasks: Array<{
    task_id: string;
    start: string | null;
    end: string | null;
    equipment: string | null;
    batch_group?: string | null;
    process_name?: string | null;
    sales_order_id?: string | null;
    customer_name?: string | null;
    sheath_color?: string | null;
    cross_section?: number | null;
    due_date?: string | null;
  }>;
  removed_tasks: Array<{
    task_id: string;
    start: string | null;
    end: string | null;
    equipment: string | null;
    batch_group?: string | null;
    process_name?: string | null;
    sales_order_id?: string | null;
    customer_name?: string | null;
    sheath_color?: string | null;
    cross_section?: number | null;
    due_date?: string | null;
  }>;
  unchanged_task_ids: string[];
}

/**
 * 버전 비교 모달 상태 + diff fetch 훅.
 * 선택된 run 과 그 parent_run_label 을 /pipeline/runs/compare 로 조회한다.
 *
 * 외부 시그니처는 page.tsx 가 사용하던 setter/state 그대로 노출.
 */
export function useRunCompare(selectedRun: string, runs: PipelineRun[]) {
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareData, setCompareData] = useState<RunCompareResponse | null>(
    null,
  );
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);

  // 이전 버전과 비교 — 선택된 run 과 그 parent_run_label 을 /runs/compare 로 조회
  const handleCompareWithParent = useCallback(async () => {
    if (!selectedRun) return;
    const current = runs.find((r) => r.run_label === selectedRun);
    const parent = current?.parent_run_label;
    if (!parent) {
      setCompareError(
        "비교 대상(parent)이 없습니다. 최초 계획 실행은 비교할 이전 버전이 없습니다.",
      );
      setCompareOpen(true);
      setCompareData(null);
      return;
    }
    setCompareLoading(true);
    setCompareError(null);
    setCompareData(null);
    setCompareOpen(true);
    try {
      const url = `${API_BASE}/pipeline/runs/compare?before=${encodeURIComponent(
        parent,
      )}&after=${encodeURIComponent(selectedRun)}`;
      const res = await fetch(url);
      if (!res.ok) {
        const body = await res
          .json()
          .catch(() => ({ detail: `HTTP ${res.status}` }));
        setCompareError(body.detail ?? `비교 실패 (${res.status})`);
        return;
      }
      const data: RunCompareResponse = await res.json();
      setCompareData(data);
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : String(err));
    } finally {
      setCompareLoading(false);
    }
  }, [selectedRun, runs]);

  return {
    compareOpen,
    setCompareOpen,
    compareData,
    setCompareData,
    compareLoading,
    compareError,
    setCompareError,
    handleCompareWithParent,
  };
}
