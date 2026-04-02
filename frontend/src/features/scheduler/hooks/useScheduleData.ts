"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/shared/api/client";
import { useScheduleStore } from "../store/scheduleStore";
import type { Equipment, ScheduleTask, LineSpeedEntry } from "../types";

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
 * 이번 주 월요일부터 +3주 금요일까지의 날짜 범위를 ISO 문자열로 반환한다.
 * 공장 수동 계획표 기준(3주 창)에 맞춰 조회 범위를 제한하여 불필요한 데이터를 줄인다.
 */
function getThreeWeekWindow(): { dateFrom: string; dateTo: string } {
  const now = new Date();
  // 이번 주 월요일 (일=0 기준 → 월=1)
  const dayOfWeek = now.getDay(); // 0=Sun, 1=Mon, ...
  const diffToMonday = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
  const monday = new Date(now);
  monday.setDate(now.getDate() + diffToMonday);
  monday.setHours(0, 0, 0, 0);

  // +3주 금요일
  const friday = new Date(monday);
  friday.setDate(monday.getDate() + 3 * 7 - 3); // +18일 = 3주 뒤 금요일
  friday.setHours(23, 59, 59, 999);

  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { dateFrom: fmt(monday), dateTo: fmt(friday) };
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

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchData() {
      setIsLoading(true);
      setError(null);
      try {
        const { dateFrom, dateTo } = getThreeWeekWindow();
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

        setEquipment(equipment);
        setTasks(rawTasks.map(parseTask));
        setLineSpeedData(lineSpeeds);
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
