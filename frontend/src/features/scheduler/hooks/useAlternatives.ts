"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "@/shared/api/client";

/**
 * GET /api/decisions/{batch_id}/alternatives 페치 훅.
 *
 * useDecisionCard 와 동일한 상태 모델 (idle/loading/ok/no-trace/timeout/error)
 * — 호출부가 single switch 로 분기 가능.
 *
 * Why 분리된 훅: alternatives 는 modal-open 시점에만 필요하고, useDecisionCard
 * 의 /latest 응답에 묶으면 응답 size 가 커진다. 별도 endpoint + 별도 훅으로
 * 두면 lazy-load 도 쉽고, 향후 alternatives 만 갱신 (재시뮬) 도 자유롭다.
 */

const FETCH_TIMEOUT_MS = 8000;

export interface Alternative {
  equipment_code: string;
  equipment_name: string;
  is_current: boolean;
  conflict_count: number;
  /** ISO-8601 — null 이면 anchor slot 부재 (수동 배치 등). */
  earliest_available: string | null;
  /** earliest_available + duration 이 due_date 초과 시 양수. null 이면 미산정. */
  delay_days: number | null;
}

export interface AlternativesData {
  batch_id: string;
  current_equipment_code: string | null;
  current_assigned_start: string | null;
  current_assigned_end: string | null;
  alternatives: Alternative[];
}

export type AlternativesStatus =
  | "idle"
  | "loading"
  | "ok"
  | "no-trace"
  | "timeout"
  | "error";

export interface AlternativesError {
  message: string;
  runId: string | null;
  status: number;
}

export interface UseAlternativesResult {
  data: AlternativesData | null;
  status: AlternativesStatus;
  error: AlternativesError | null;
  refetch: () => void;
}

interface ResolvedResult {
  batchId: string;
  refetchKey: number;
  status: Exclude<AlternativesStatus, "idle" | "loading">;
  data: AlternativesData | null;
  error: AlternativesError | null;
}

export function useAlternatives(batchId: string | null): UseAlternativesResult {
  const [refetchKey, setRefetchKey] = useState(0);
  const [resolved, setResolved] = useState<ResolvedResult | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const refetch = useCallback(() => {
    setRefetchKey((k) => k + 1);
  }, []);

  useEffect(() => {
    if (batchId === null) return;

    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    let timedOut = false;
    const timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, FETCH_TIMEOUT_MS);

    const reqBatchId = batchId;
    const reqKey = refetchKey;

    apiFetch<AlternativesData>(
      `/decisions/${encodeURIComponent(batchId)}/alternatives`,
      { signal: controller.signal },
    )
      .then((res) => {
        clearTimeout(timeoutId);
        if (controller.signal.aborted) return;
        setResolved({
          batchId: reqBatchId,
          refetchKey: reqKey,
          status: "ok",
          data: res,
          error: null,
        });
      })
      .catch((e: unknown) => {
        clearTimeout(timeoutId);
        if (controller.signal.aborted && !timedOut) return;

        if (timedOut) {
          setResolved({
            batchId: reqBatchId,
            refetchKey: reqKey,
            status: "timeout",
            data: null,
            error: {
              message: "네트워크 응답이 느립니다",
              runId: null,
              status: 0,
            },
          });
          return;
        }

        if (e instanceof ApiError) {
          if (e.status === 404) {
            setResolved({
              batchId: reqBatchId,
              refetchKey: reqKey,
              status: "no-trace",
              data: null,
              error: null,
            });
            return;
          }
          setResolved({
            batchId: reqBatchId,
            refetchKey: reqKey,
            status: "error",
            data: null,
            error: { message: e.message, runId: e.runId, status: e.status },
          });
          return;
        }

        const msg = e instanceof Error ? e.message : "알 수 없는 네트워크 오류";
        setResolved({
          batchId: reqBatchId,
          refetchKey: reqKey,
          status: "error",
          data: null,
          error: { message: msg, runId: null, status: 0 },
        });
      });

    return () => {
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [batchId, refetchKey]);

  if (batchId === null) {
    return { data: null, status: "idle", error: null, refetch };
  }
  if (
    resolved === null ||
    resolved.batchId !== batchId ||
    resolved.refetchKey !== refetchKey
  ) {
    return { data: null, status: "loading", error: null, refetch };
  }
  return {
    data: resolved.data,
    status: resolved.status,
    error: resolved.error,
    refetch,
  };
}
