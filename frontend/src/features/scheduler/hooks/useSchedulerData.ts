/**
 * useSchedulerData — scheduler 페이지의 파생 데이터 + 부수 fetch 를 단일 hook 으로 응집.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - 페이지 진입 시 최신 run_label 조회 → SM 모달용 + scheduleStore 의 runLabel 저장
 *     (AI 재분석 트리거 의존)
 *   - viewFilter 에 반응하는 visibleEquipmentIds 계산
 *   - 납기 초과 task 목록 + 지연 일수 정렬 (lateTasks)
 *
 * 비고:
 *   - 핵심 도메인 fetch (equipment / tasks / lineSpeeds) 는 기존 useScheduleData() 가 담당.
 *     이 hook 은 그 위에서 "페이지에서만 필요한" 파생값을 묶는다.
 *   - 반환 타입은 페이지가 destructure 하기 쉽도록 평면화.
 */
"use client";

import { useEffect, useMemo, useState } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import { useScheduleData } from "./useScheduleData";
import {
  filterEquipmentByView,
  taskMatchesViewFilter,
} from "../utils/viewFilter";
import type { ScheduleTask } from "../types";

const API_BASE = "http://localhost:8000/api";

export interface LateTaskEntry {
  task: ScheduleTask;
  /** 납기일을 넘긴 일 수 (배치 종료 시각 - 납기일자정, ceil) */
  lateDays: number;
}

export interface UseSchedulerDataResult {
  /** useScheduleData() 의 isLoading 그대로 전달 */
  isLoading: boolean;
  /** useScheduleData() 의 error 그대로 전달 */
  error: string | null;
  /** SM재고 실적 모달이 사용할 최신 run_label (없으면 null) */
  wipRunLabel: string | null;
  /** 현재 viewFilter 가 노출하는 설비 id 집합 (lateTasks 계산용) */
  visibleEquipmentIds: Set<string>;
  /** 납기 초과 배치 목록 — lateDays 내림차순 */
  lateTasks: LateTaskEntry[];
}

export function useSchedulerData(): UseSchedulerDataResult {
  const { isLoading, error } = useScheduleData();

  const tasks = useScheduleStore((s) => s.tasks);
  const equipment = useScheduleStore((s) => s.equipment);
  const viewFilter = useScheduleStore((s) => s.viewFilter);
  const setRunLabel = useScheduleStore((s) => s.setRunLabel);

  // SM재고 실적 모달용 최신 run_label.
  // scheduleStore.runLabel 도 동시에 set 해서 AI 재분석 trigger 가 사용한다.
  const [wipRunLabel, setWipRunLabel] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/pipeline/runs`)
      .then((r) => (r.ok ? r.json() : []))
      .then((runs: Array<{ run_label: string }>) => {
        if (runs.length > 0) {
          setWipRunLabel(runs[0].run_label);
          setRunLabel(runs[0].run_label);
        }
      })
      .catch(() => {});
  }, [setRunLabel]);

  // viewFilter(전체/저압만/고압만/공정별) 에 반응하는 설비 id 집합.
  const visibleEquipmentIds = useMemo(
    () =>
      new Set(
        filterEquipmentByView(
          equipment,
          viewFilter.filterType,
          viewFilter.filterValue,
        ).map((eq) => eq.id),
      ),
    [equipment, viewFilter.filterType, viewFilter.filterValue],
  );

  // 납기 초과 task 목록 — 배치 종료 시각 > 납기일 23:59:59.999.
  const lateTasks = useMemo<LateTaskEntry[]>(() => {
    return tasks
      .filter((t) => taskMatchesViewFilter(t, visibleEquipmentIds))
      .filter((t) => {
        if (!t.delivery_date) return false;
        const dd =
          t.delivery_date instanceof Date
            ? t.delivery_date
            : new Date(t.delivery_date);
        const due = new Date(dd);
        due.setHours(23, 59, 59, 999);
        const endTs =
          t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime();
        return endTs > due.getTime();
      })
      .map((t) => {
        const dd =
          t.delivery_date instanceof Date
            ? t.delivery_date
            : new Date(t.delivery_date!);
        const due = new Date(dd);
        due.setHours(23, 59, 59, 999);
        const endTs =
          t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime();
        const lateDays = Math.ceil(
          (endTs - due.getTime()) / (24 * 60 * 60 * 1000),
        );
        return { task: t, lateDays };
      })
      .sort((a, b) => b.lateDays - a.lateDays);
  }, [tasks, visibleEquipmentIds]);

  return { isLoading, error, wipRunLabel, visibleEquipmentIds, lateTasks };
}
