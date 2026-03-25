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
 * 백엔드 API에서 설비 목록, 스케줄 작업, 라인 속도 데이터를 불러와 Zustand 스토어에 저장한다.
 * GET /api/equipment, GET /api/schedules/tasks, GET /api/line-speeds 를 동시에 호출한다.
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
        const [equipment, rawTasks, lineSpeeds] = await Promise.all([
          apiFetch<Equipment[]>("/api/equipment"),
          apiFetch<RawScheduleTask[]>("/api/schedules/tasks"),
          apiFetch<LineSpeedEntry[]>("/api/line-speeds").catch(() => {
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
