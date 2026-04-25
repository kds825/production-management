/**
 * useAutoSchedule — Stage 2 자동배열 비동기 job 관리.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - 최신 run_label 조회 → /pipeline/stage2/async POST → job_id 폴링.
 *   - 진행/완료/오류/오버랩 상태에 따라 banner 메시지(autoScheduleResult) 또는
 *     overlapAlert 갱신.
 *   - 완료 시 refreshScheduleTasks() 로 부분 갱신 (페이지 reload 회피).
 *
 * 왜 비동기 경로:
 *   동기 /pipeline/stage2 는 10분 급 솔빙 동안 HTTP 연결을 유지하며 워커를 점유.
 *   /stage2/async 는 job_id 를 즉시 반환 → 1초 간격 status polling.
 *   기존 응답 구조(overlap_alert, schedule 등)는 job.result 에 그대로 실려 옴.
 */
"use client";

import { useCallback, useState } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import { refreshTasks as refreshScheduleTasks } from "./useScheduleData";

const API_BASE = "http://localhost:8000/api";

export interface UseAutoScheduleResult {
  autoScheduleLoading: boolean;
  autoScheduleResult: string | null;
  setAutoScheduleResult: (msg: string | null) => void;
  overlapAlert: string | null;
  setOverlapAlert: (msg: string | null) => void;
  handleAutoSchedule: () => Promise<void>;
}

type JobStatus = {
  status: "running" | "done" | "overlap_alert" | "error";
  result?: {
    run_label?: string;
    overlap_alert?: boolean;
    attempts?: number;
    message?: string;
  };
  error?: string;
  started_at?: string;
};

export function useAutoSchedule(): UseAutoScheduleResult {
  const setRunLabel = useScheduleStore((s) => s.setRunLabel);

  const [autoScheduleLoading, setAutoScheduleLoading] = useState(false);
  const [autoScheduleResult, setAutoScheduleResult] = useState<string | null>(
    null,
  );
  // Stage 2 API 가 `overlap_alert: true` 를 반환했을 때 상단 배너에 표시할 메시지.
  // 자동배열이 재시도 한도 내에 non-overlapping 스케줄을 만들지 못한 경우 기존
  // 스케줄을 유지(refresh 안 함)하고 사용자에게 명시적으로 경고한다.
  const [overlapAlert, setOverlapAlert] = useState<string | null>(null);

  const handleAutoSchedule = useCallback(async () => {
    setAutoScheduleLoading(true);
    setAutoScheduleResult(null);
    // 새 실행 시작 시 이전 겹침 배너는 제거 (성공/실패는 아래 분기에서 재설정)
    setOverlapAlert(null);
    try {
      // 최신 런 라벨 조회
      const runsRes = await fetch(`${API_BASE}/pipeline/runs`);
      let runLabel: string | null = null;
      if (runsRes.ok) {
        const runs: Array<{ run_label: string }> = await runsRes.json();
        if (runs.length > 0) runLabel = runs[0].run_label;
      }
      if (!runLabel) {
        setAutoScheduleResult(
          "실행 가능한 런이 없습니다. 먼저 Stage 1을 실행하세요.",
        );
        return;
      }
      // scheduleStore 에 runLabel 저장 (AI 재분석 트리거용)
      setRunLabel(runLabel);

      const submitRes = await fetch(`${API_BASE}/pipeline/stage2/async`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_label: runLabel,
          base_date:
            typeof window !== "undefined"
              ? localStorage.getItem("plan_base_date") || undefined
              : undefined,
        }),
      });
      if (!submitRes.ok) {
        const text = await submitRes.text();
        setAutoScheduleResult(
          `오류: ${submitRes.status} — ${text.slice(0, 120)}`,
        );
        return;
      }
      const { job_id: jobId } = (await submitRes.json()) as { job_id: string };

      // ── 폴링 루프: 1초 간격, 최대 20분 ───────────────────────────────
      const startedAt = Date.now();
      const deadlineMs = 20 * 60 * 1000;
      let finalStatus: JobStatus | null = null;
      while (Date.now() - startedAt < deadlineMs) {
        await new Promise((r) => setTimeout(r, 1000));
        const statusRes = await fetch(
          `${API_BASE}/pipeline/stage2/status/${jobId}`,
        );
        if (!statusRes.ok) {
          setAutoScheduleResult(`상태 조회 실패: ${statusRes.status}`);
          return;
        }
        const s = (await statusRes.json()) as JobStatus;
        if (s.status === "running") {
          const elapsedS = Math.floor((Date.now() - startedAt) / 1000);
          setAutoScheduleResult(`자동배열 진행 중... ${elapsedS}초 경과`);
          continue;
        }
        finalStatus = s;
        break;
      }

      if (!finalStatus) {
        setAutoScheduleResult("자동배열 타임아웃 (20분). 서버 로그 확인 필요.");
        return;
      }

      if (finalStatus.status === "error") {
        setAutoScheduleResult(
          `자동배열 오류: ${finalStatus.error ?? "알 수 없음"}`,
        );
        return;
      }

      if (
        finalStatus.status === "overlap_alert" ||
        finalStatus.result?.overlap_alert
      ) {
        const attempts = finalStatus.result?.attempts ?? 3;
        setOverlapAlert(`${attempts}회 재시도 실패. 기존 스케줄을 유지합니다.`);
        setAutoScheduleResult(null);
        return;
      }

      setAutoScheduleResult(`자동배열 완료 (런: ${runLabel}).`);
      // window.location.reload() 대신 부분 갱신:
      //   reload 는 모든 useEffect 를 재실행시켜 /pipeline/runs, /schedules/tasks,
      //   /equipment, /line-speeds, /audit 등 10+ 엔드포인트가 동시 재호출되어
      //   Supabase 커넥션 풀에 폭주 트래픽을 만든다. 대신 scheduler 페이지가
      //   실제로 관심 있는 태스크만 다시 가져와 store 에 반영하면, Gantt 가
      //   동일 effect 체인 없이 즉시 재렌더링된다.
      await refreshScheduleTasks();
    } catch {
      setAutoScheduleResult(
        `연결 실패: 백엔드 서버(localhost:8000)를 확인하세요.`,
      );
    } finally {
      setAutoScheduleLoading(false);
    }
  }, [setRunLabel]);

  return {
    autoScheduleLoading,
    autoScheduleResult,
    setAutoScheduleResult,
    overlapAlert,
    setOverlapAlert,
    handleAutoSchedule,
  };
}
