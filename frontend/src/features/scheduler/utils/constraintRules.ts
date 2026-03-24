/**
 * constraintRules.ts
 *
 * 클라이언트 사이드 제약 조건 검증 로직.
 * 드래그 핸들러 및 TaskFormModal에서 사용한다.
 */

import type { ScheduleTask, ConstraintViolation, Equipment } from "../types";

/** 두 작업이 동일 설비에서 시간 범위가 겹치는지 확인 */
function hasOverlap(a: ScheduleTask, b: ScheduleTask): boolean {
  if (a.equipment_id !== b.equipment_id) return false;
  const aStart = new Date(a.start).getTime();
  const aEnd = new Date(a.end).getTime();
  const bStart = new Date(b.start).getTime();
  const bEnd = new Date(b.end).getTime();
  return aStart < bEnd && bStart < aEnd;
}

/**
 * 단일 작업에 대한 제약 조건 위반을 검사하고 반환한다.
 *
 * @param task     검사 대상 작업
 * @param allTasks 기존 모든 작업 (본인 제외)
 * @param equipment 설비 목록
 */
export function validateTask(
  task: ScheduleTask,
  allTasks: ScheduleTask[],
  equipment: Equipment[],
): ConstraintViolation[] {
  const violations: ConstraintViolation[] = [];
  const others = allTasks.filter((t) => t.id !== task.id);

  // 1. 설비 가용성 (capability) 검사
  const eq = equipment.find((e) => e.id === task.equipment_id);
  if (eq && task.spec) {
    const capable = eq.capabilities.some(
      (cap) => task.spec.includes(cap) || task.product.includes(cap),
    );
    if (!capable && eq.capabilities.length > 0) {
      violations.push({
        type: "equipment_capability",
        severity: "error",
        message: `${eq.name} 설비는 ${task.product} 제품을 처리할 수 없습니다.`,
        task_id: task.id,
      });
    }
  }

  // 2. 동일 설비 중첩 검사
  for (const other of others) {
    if (hasOverlap(task, other)) {
      violations.push({
        type: "overlap",
        severity: "error",
        message: `작업 "${task.product}"가 "${other.product}"와 설비 점유 시간이 겹칩니다.`,
        task_id: task.id,
        related_task_id: other.id,
      });
    }
  }

  // 3. 납기일 검사
  if (task.delivery_date) {
    const end = new Date(task.end).getTime();
    const delivery = new Date(task.delivery_date).getTime();
    if (end > delivery) {
      violations.push({
        type: "delivery",
        severity: "warning",
        message: `작업 "${task.product}"의 완료 예정일이 납기일(${new Date(task.delivery_date).toLocaleDateString("ko-KR")})을 초과합니다.`,
        task_id: task.id,
      });
    }
  }

  // 4. 선행 작업 순서 검사
  for (const predId of task.predecessors) {
    const pred = allTasks.find((t) => t.id === predId);
    if (!pred) continue;

    const predEnd = new Date(pred.end).getTime();
    const taskStart = new Date(task.start).getTime();
    if (predEnd > taskStart) {
      violations.push({
        type: "precedence",
        severity: "error",
        message: `선행 작업 "${pred.product}"가 완료되기 전에 "${task.product}"이 시작됩니다.`,
        task_id: task.id,
        related_task_id: pred.id,
      });
    }
  }

  return violations;
}

/**
 * 전체 작업 목록에 대한 제약 조건 검증을 수행하고 모든 위반을 반환한다.
 */
export function validateAllTasks(
  tasks: ScheduleTask[],
  equipment: Equipment[],
): ConstraintViolation[] {
  const allViolations: ConstraintViolation[] = [];
  for (const task of tasks) {
    const violations = validateTask(task, tasks, equipment);
    allViolations.push(...violations);
  }
  // 중복 제거 (같은 task_id + type 조합)
  const seen = new Set<string>();
  return allViolations.filter((v) => {
    const key = `${v.task_id}:${v.type}:${v.related_task_id ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
