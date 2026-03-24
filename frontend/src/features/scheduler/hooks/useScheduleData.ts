"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/shared/api/client";
import { useScheduleStore } from "../store/scheduleStore";
import type { Equipment, ScheduleTask } from "../types";

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
 * 백엔드 API에서 설비 목록과 스케줄 작업을 불러와 Zustand 스토어에 저장한다.
 * GET /api/equipment, GET /api/schedules 를 동시에 호출한다.
 */
export function useScheduleData() {
  const setEquipment = useScheduleStore((s) => s.setEquipment);
  const setTasks = useScheduleStore((s) => s.setTasks);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchData() {
      setIsLoading(true);
      setError(null);
      try {
        const [equipment, rawTasks] = await Promise.all([
          apiFetch<Equipment[]>("/api/equipment"),
          apiFetch<RawScheduleTask[]>("/api/schedules/tasks"),
        ]);

        if (cancelled) return;

        setEquipment(equipment);
        setTasks(rawTasks.map(parseTask));
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
  }, [setEquipment, setTasks]);

  return { isLoading, error };
}
