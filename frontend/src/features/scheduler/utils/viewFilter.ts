/**
 * 뷰 필터 (전체 / 저압만 / 고압만 / 공정별) 적용을 위한 순수 유틸.
 *
 * SchedulerView 의 `useFilteredEquipment` 훅과 동일한 규칙을 순수 함수로 노출하여
 * 납기초과 카운터 등 React hook 바깥에서도 동일 기준으로 쓰게 한다 (SSOT).
 *
 * 규칙 요약:
 *   - filterType="all"      → 신선 제외 전 설비
 *   - filterType="voltage"  → filterValue 에 포함된 설비 id 만
 *                              (빈 배열은 all 과 동등 — ViewFilter 의 초기화 경로)
 *   - filterType="process"  → filterValue 에 포함된 process_type 만
 *                              (빈 배열은 all 과 동등)
 *   - 어떤 필터든 process_type === "신선" 은 항상 숨김 (간트에서 배치 미생성 대상).
 */
import type { Equipment, ScheduleTask, ViewFilterType } from "../types";

export function filterEquipmentByView(
  equipment: Equipment[],
  filterType: ViewFilterType,
  filterValue: string[],
): Equipment[] {
  const withoutDrawing = equipment.filter((eq) => eq.process_type !== "신선");
  if (filterType === "voltage") {
    return filterValue.length === 0
      ? withoutDrawing
      : withoutDrawing.filter((eq) => filterValue.includes(eq.id));
  }
  if (filterType === "process") {
    return filterValue.length === 0
      ? withoutDrawing
      : withoutDrawing.filter((eq) => filterValue.includes(eq.process_type));
  }
  return withoutDrawing; // "all"
}

/**
 * task 가 현재 뷰 필터에 포함되는지. filteredEquipmentIds 가 task.equipment_id 를
 * 포함하면 true. 사전 계산된 id Set 을 넘겨 O(1) 매칭.
 */
export function taskMatchesViewFilter(
  task: ScheduleTask,
  filteredEquipmentIds: Set<string>,
): boolean {
  return filteredEquipmentIds.has(task.equipment_id);
}
