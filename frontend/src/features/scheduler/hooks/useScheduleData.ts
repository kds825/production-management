"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/shared/api/client";
import { useScheduleStore } from "../store/scheduleStore";
import type { Equipment, ScheduleTask, LineSpeedEntry } from "../types";
import { getDefaultRange } from "../utils/ganttUtils";

interface RawScheduleTask extends Omit<
  ScheduleTask,
  "start" | "end" | "delivery_date"
> {
  start: string;
  end: string;
  delivery_date?: string;
}

/** ISO 문자열을 Date로 변환 */
function parseTask(raw: RawScheduleTask): ScheduleTask {
  return {
    ...raw,
    start: new Date(raw.start),
    end: new Date(raw.end),
    delivery_date: raw.delivery_date ? new Date(raw.delivery_date) : undefined,
  };
}

/**
 * 계획 기준일(plan_base_date)로부터 +8주까지의 날짜 범위를 반환한다.
 * 납기가 긴 대형 SQ 배치(300SQ+)가 뒤늦게 배치될 수 있으므로 충분히 넓게 잡는다.
 */
function getFetchWindow(): { dateFrom: string; dateTo: string } {
  const stored = typeof window !== "undefined" ? localStorage.getItem("plan_base_date") : null;
  let base: Date;
  if (stored && stored.length === 8) {
    base = new Date(`${stored.slice(0, 4)}-${stored.slice(4, 6)}-${stored.slice(6, 8)}`);
  } else {
    base = new Date();
  }
  base.setHours(0, 0, 0, 0);
  const end = new Date(base);
  end.setDate(base.getDate() + 56); // +8주
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { dateFrom: fmt(base), dateTo: fmt(end) };
}

/**
 * 백엔드 API에서 설비 목록, 스케줄 작업, 라인 속도 데이터를 불러와 Zustand 스토어에 저장한다.
 * GET /api/equipment, GET /api/schedules/tasks, GET /api/line-speeds 를 동시에 호출한다.
 * tasks는 3주 창(이번 주 월 ~ +3주 금) 으로 필터링하여 약 100~200건만 수신한다.
 */
export function useScheduleData() {
  const setEquipment = useScheduleStore((s) => s.setEquipment);
  const setTasks = useScheduleStore((s) => s.setTasks);
  const setLineSpeedData = useScheduleStore((s) => s.setLineSpeedData);
  const setRange = useScheduleStore((s) => s.setRange);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchData() {
      setIsLoading(true);
      setError(null);
      try {
        const { dateFrom, dateTo } = getFetchWindow();
        const tasksUrl = `/schedules/tasks?date_from=${dateFrom}&date_to=${dateTo}`;

        const [equipment, rawTasks, lineSpeeds] = await Promise.all([
          apiFetch<Equipment[]>("/equipment"),
          apiFetch<RawScheduleTask[]>(tasksUrl),
          apiFetch<LineSpeedEntry[]>("/line-speeds").catch(() => {
            // 라인 속도 API가 없을 경우 빈 배열로 폴백
            console.warn(
              "[useScheduleData] 라인 속도 API 응답 없음 — 기본값 사용",
            );
            return [] as LineSpeedEntry[];
          }),
        ]);

        if (cancelled) return;

        const tasks = rawTasks.map(parseTask);
        setEquipment(equipment);
        setTasks(tasks);
        setLineSpeedData(lineSpeeds);

        // 계획 기준일자로 간트 뷰 자동 이동 — 가장 이른 task 시작 시각 기준
        if (tasks.length > 0) {
          const minStart = Math.min(...tasks.map((t) => t.start.getTime()));
          const FOUR_WEEKS_MS = 28 * 24 * 60 * 60 * 1000;
          setRange({ start: minStart, end: minStart + FOUR_WEEKS_MS });
        } else {
          setRange(getDefaultRange(14));
        }
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "데이터 로드 실패";
        setError(msg);
        console.error("[useScheduleData] 데이터 로드 오류:", err);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    fetchData();
    return () => {
      cancelled = true;
    };
  }, [setEquipment, setTasks, setLineSpeedData]);

  return { isLoading, error };
}
