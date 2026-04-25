"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "@/shared/api/client";

/**
 * Week 4 Task 4B.3 — Decision Card 데이터 훅.
 *
 * GET /api/decisions/{batch_id}/latest 를 호출해 솔버 트레이스 + LLM 요약을 가져온다.
 *
 * 상태 모델 (data + status union):
 *  - status="loading":     첫 fetch 진행 중
 *  - status="ok":          data 보유 — happy path
 *  - status="no-trace":    HTTP 404 — 트레이스 없음 (예: 수동 배치)
 *  - status="timeout":     8s AbortController 만료 — 네트워크 느림
 *  - status="error":       기타 ApiError / 네트워크 실패
 *  - status="idle":        batchId === null — 아직 어떤 배치도 선택 안됨
 *
 * 왜 status union 으로 분리했는가:
 *  - "no-trace"(404) 는 에러가 아니라 정상 비어있음 — 빨간색 토스트로 띄우면 안됨
 *  - "timeout" 은 재시도 가능 — refetch 를 노출
 *  - 호출부가 status 만 보고 단일 switch 로 분기 가능 (계약 명시적)
 *
 * Run-Id 보존: ApiError.runId 는 그대로 error.runId 에 보존되어
 * Toast.tsx 에 전달 가능 (운영자 코릴레이션 ID 패턴).
 */

const FETCH_TIMEOUT_MS = 8000;

/** 백엔드 응답 스키마 — Week 4 Task 4B.3 backend (33d3e0f) 와 일치해야 함. */
export interface DecisionContribution {
  constraint_id: string;
  korean_name: string;
  weight_applied: number;
  bound: number | null;
  delta_if_removed: number | null;
}

export interface DecisionBindingHardConstraint {
  constraint_id: string;
  korean_name: string;
}

export interface DecisionManualOverride {
  change_set_id: string;
  reason: string;
  snapshot_before: {
    assigned_equipment_id: string | null;
    assigned_start: string | null;
    assigned_end: string | null;
  };
}

export type SolverStatus = "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN";

export interface DecisionData {
  batch_id: string;
  run_id: string;
  run_label: string;
  solver_status: SolverStatus;
  objective_value: number | null;
  assigned_equipment_id: string | null;
  assigned_start: string | null;
  assigned_end: string | null;
  contributions: DecisionContribution[];
  binding_hard_constraints: DecisionBindingHardConstraint[];
  is_manually_adjusted: boolean;
  manual_override: DecisionManualOverride | null;
  llm_summary: string;
  llm_was_template: boolean;
}

export type DecisionCardStatus =
  | "idle"
  | "loading"
  | "ok"
  | "no-trace"
  | "timeout"
  | "error";

export interface DecisionCardError {
  /** 사람이 읽을 수 있는 메시지 (한국어) */
  message: string;
  /** 백엔드 X-Run-Id (있다면) — Toast 에 그대로 전달해 운영자 코릴레이션. */
  runId: string | null;
  /** HTTP status (network 실패 시 0) */
  status: number;
}

export interface UseDecisionCardResult {
  data: DecisionData | null;
  status: DecisionCardStatus;
  error: DecisionCardError | null;
  refetch: () => void;
}

/**
 * batchId 가 null 이면 idle 상태 — 네트워크 호출 없음.
 *
 * AbortController 정책:
 *  - 8s 만료 → controller.abort() → status="timeout"
 *  - batchId 가 변경되면 in-flight 요청 abort (race-condition 방지)
 *  - 컴포넌트 언마운트 시 abort
 */
/**
 * 비동기 fetch 결과 1건의 응답 — 현재 보고 있는 batchId/refetchKey 에 매칭될 때만 표시.
 *
 * 왜 이 구조인가:
 *   react-hooks/immutability 룰이 effect body 내부의 동기 setState 를 금지한다
 *   (cascading renders 회피). batchId 변경 시 보여야 할 "loading" 은 setState
 *   대신 "현재 input 과 lastResolved 가 일치하는가?" 로 파생해 즉시 반영한다.
 *   → 효과는 오직 async 콜백 (then/catch/timeout) 에서만 setState 호출.
 */
interface ResolvedResult {
  /** 어떤 batchId 에 대한 결과인가 — 현재 input 과 비교용. */
  batchId: string;
  /** 어떤 refetchKey 시점의 결과인가 — refetch 후 stale 결과 무시용. */
  refetchKey: number;
  status: Exclude<DecisionCardStatus, "idle" | "loading">;
  data: DecisionData | null;
  error: DecisionCardError | null;
}

export function useDecisionCard(batchId: string | null): UseDecisionCardResult {
  /**
   * refetchKey: 사용자가 "재시도" 버튼을 눌렀을 때 useEffect 를 강제 재실행.
   * batchId 가 동일해도 effect 가 다시 돌도록 의존성에 포함.
   */
  const [refetchKey, setRefetchKey] = useState(0);

  /**
   * 마지막으로 도착한 응답. (batchId, refetchKey) 가 현재 입력과 일치할 때만 표시.
   * 입력이 바뀌면 자동으로 "loading" 으로 파생됨 — setState 동기 호출 불필요.
   */
  const [resolved, setResolved] = useState<ResolvedResult | null>(null);

  /** 진행 중인 controller — 새 요청 시작 / 언마운트 시 abort 용. */
  const controllerRef = useRef<AbortController | null>(null);

  const refetch = useCallback(() => {
    setRefetchKey((k) => k + 1);
  }, []);

  useEffect(() => {
    if (batchId === null) return;

    // 이전 in-flight 요청 중단 (batchId 변경 또는 refetch 트리거).
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    // 8s timeout — setTimeout 으로 controller.abort() 트리거.
    // AbortError 와 timeout 을 구분하기 위해 timedOut flag 보존.
    let timedOut = false;
    const timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, FETCH_TIMEOUT_MS);

    // 캡쳐: 이 요청을 식별할 (batchId, refetchKey) 쌍 — 응답 도착 시 stale 가드.
    const reqBatchId = batchId;
    const reqKey = refetchKey;

    apiFetch<DecisionData>(`/decisions/${encodeURIComponent(batchId)}/latest`, {
      signal: controller.signal,
    })
      .then((res) => {
        clearTimeout(timeoutId);
        if (controller.signal.aborted) return; // 이미 superseded
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
        // 새 요청이 시작되어 abort 된 경우 — 결과 무시 (race-condition 방지).
        // timedOut=true 인 경우만 timeout 상태로 분기.
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
            // 404 는 정상 비어있음 — 에러 토스트 띄우지 않도록 status 분리.
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

        // 네트워크 단절 / DNS 실패 등 fetch 자체가 throw — Error 인스턴스
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

  // 표시할 상태 파생: input 과 마지막 응답이 일치하지 않으면 loading/idle.
  // setState 동기 호출 없음 — react-hooks/immutability 룰 준수.
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
