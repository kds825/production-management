"use client";

/**
 * Phase 6 Step 4-MVP — DecisionCard v2 fetch hook.
 *
 * GET /api/scheduler/{run_label}/decision-card/{batch_id}?debug={0|1}
 *
 * 헤더:
 *   X-User-Role: 'operator' | 'admin' (PoC — JWT 미설정. localStorage
 *   'kbi.user_role' 우선, 미지정 시 'admin' default = 백엔드 PoC 일치)
 *
 * 상태 union (기존 v1 useDecisionCard 와 같은 contract):
 *   - 'idle': batchId/runLabel null
 *   - 'loading': 첫 fetch
 *   - 'ok': data
 *   - 'no-trace': 404 (배치 미존재 / run_label 불일치)
 *   - 'timeout': 8s AbortController
 *   - 'error': 기타
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "@/shared/api/client";

import type { DecisionCardV2 } from "../components/decision/decisionCardTypes";

const FETCH_TIMEOUT_MS = 8000;

export type V2Status =
  | "idle"
  | "loading"
  | "ok"
  | "no-trace"
  | "timeout"
  | "error";

export interface UseDecisionCardV2Result {
  data: DecisionCardV2 | null;
  status: V2Status;
  error: ApiError | null;
  refetch: () => void;
}

function readUserRole(): string {
  if (typeof window === "undefined") return "admin";
  return window.localStorage.getItem("kbi.user_role") || "admin";
}

/** ?debug=1 URL 쿼리 또는 localStorage 'kbi.debug_mode' = '1' → debug 요청. */
export function readDebugMode(): boolean {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  if (params.get("debug") === "1") return true;
  return window.localStorage.getItem("kbi.debug_mode") === "1";
}

interface Args {
  runLabel: string | null;
  batchId: number | null;
  debug?: boolean;
}

export function useDecisionCardV2({
  runLabel,
  batchId,
  debug = false,
}: Args): UseDecisionCardV2Result {
  const [data, setData] = useState<DecisionCardV2 | null>(null);
  const [status, setStatus] = useState<V2Status>("idle");
  const [error, setError] = useState<ApiError | null>(null);
  const reqRef = useRef(0);

  const run = useCallback(async () => {
    if (!runLabel || batchId == null) {
      setStatus("idle");
      setData(null);
      setError(null);
      return;
    }
    const myReq = ++reqRef.current;
    setStatus("loading");
    setError(null);

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);

    try {
      const path = `/scheduler/${encodeURIComponent(runLabel)}/decision-card/${batchId}?debug=${debug ? 1 : 0}`;
      const json = await apiFetch<DecisionCardV2>(path, {
        signal: ctrl.signal,
        headers: { "X-User-Role": readUserRole() },
      });
      if (myReq !== reqRef.current) return;
      setData(json);
      setStatus("ok");
    } catch (e) {
      if (myReq !== reqRef.current) return;
      if (e instanceof DOMException && e.name === "AbortError") {
        setStatus("timeout");
        return;
      }
      if (e instanceof ApiError) {
        if (e.status === 404) {
          setStatus("no-trace");
          return;
        }
        setError(e);
        setStatus("error");
        return;
      }
      setStatus("error");
    } finally {
      clearTimeout(timer);
    }
  }, [runLabel, batchId, debug]);

  useEffect(() => {
    run();
  }, [run]);

  return { data, status, error, refetch: run };
}
