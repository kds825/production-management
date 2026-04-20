/**
 * 간트 diff overlay 를 위한 stable-key 인덱서.
 *
 * `/api/pipeline/runs/compare` 응답을 프론트에서 즉시 조회 가능한
 * Map<stableKey, DiffIndexEntry> 로 변환한다.
 *
 * ## stable key 규약
 *
 * 백엔드 `/runs/compare` 에서 사용하는 task_id 와 **완전히 동일한 문자열** 을 사용한다:
 *
 *   "{sales_order_id || batch_group}|{sales_order_line || 0}|{process_name || ''}|{batch_seq || 0}"
 *
 * 이렇게 맞춰야 프론트 ScheduleTask 에서 동일 key 로 Map lookup 이 성립한다.
 * 헤더 배치처럼 sales_order_id 가 비어 있는 경우 batch_group 으로 fallback 한다
 * (백엔드도 동일 fallback — 스펙 Risk "stable key 불일치" 참조).
 *
 * ## 왜 문자열 key?
 *
 * 튜플 키는 JS Map 에서 참조 동일성 이슈가 있어 매번 새 key 를 만들면 lookup 실패.
 * 문자열화하면 O(1) 해시 lookup 이 보장된다. 파이프 구분자는 key 구성요소에
 * 나타날 수 없는 안전한 문자 (sales_order_id 는 숫자/코드, process_name 은 한글).
 */
import type {
  RunCompareAddedOrRemovedTask,
  RunCompareMovedTask,
  RunCompareResponse,
} from "../types/diff";

export type DiffKind = "added" | "moved" | "removed";

/**
 * 한 블록에 대해 UI 가 필요로 하는 diff 정보.
 *
 * moved 전용 필드(old_*, new_*, start_delta_hours, equipment_changed) 는 kind='moved' 일 때만 채워진다.
 * 이외 kind 에서 이 필드를 참조하지 말 것 — undefined 다.
 * 메타 필드(batch_group, process_name 등) 는 세 kind 공통으로 채울 수 있으면 채운다
 * (백엔드 응답에 포함된 경우에 한함).
 */
export interface DiffIndexEntry {
  kind: DiffKind;
  task_id: string;
  // moved 전용 — ISO 문자열을 Date 로 미리 파싱해 렌더 루프 오버헤드 제거
  old_start?: Date;
  old_end?: Date;
  old_equipment?: string;
  new_start?: Date;
  new_end?: Date;
  new_equipment?: string;
  start_delta_hours?: number;
  equipment_changed?: boolean;
  // 메타 (세 kind 공통, 있으면 채움)
  batch_group?: string | null;
  process_name?: string | null;
  sales_order_id?: string | null;
  customer_name?: string | null;
  sheath_color?: string | null;
  cross_section?: number | null;
}

/**
 * ScheduleTask (또는 유사 shape) 로부터 stable key 문자열을 생성한다.
 *
 * 프론트 ScheduleTask 타입은 백엔드 compare 응답과 필드명이 살짝 다르다
 * (ScheduleTask.batch_id vs batch_seq, order_id 에 line 포함 가능 등).
 * 이 함수는 그 불일치를 흡수하도록 **모든 key 구성요소를 명시적으로 받는다** —
 * 호출부가 자기 shape 에서 직접 매핑해 전달한다 (fail-fast: 필드 누락 시 빈 문자열 fallback).
 *
 * @example
 *   // ScheduleTask 에서 호출
 *   taskStableKey({
 *     sales_order_id: task.order_id,
 *     sales_order_line: task.line,          // 프론트엔 없으면 undefined
 *     process_name: task.process_name,
 *     batch_seq: task.batch_seq,
 *     batch_group: task.batch_group,
 *   })
 */
export function taskStableKey(t: {
  sales_order_id?: string | null;
  sales_order_line?: number | null;
  process_name?: string | null;
  batch_seq?: number | null;
  batch_group?: string | null;
  batch_id?: string | number;
}): string {
  // sales_order_id 우선, 비어있으면 batch_group fallback (백엔드 동일 규칙).
  // 둘 다 없으면 key 는 "|0||0" 꼴이 되어 사실상 매칭 불가 — 정상 (미기재 상태).
  const oid =
    (t.sales_order_id ?? "") !== "" ? t.sales_order_id : (t.batch_group ?? "");
  const line = t.sales_order_line ?? 0;
  const process = t.process_name ?? "";
  const seq = t.batch_seq ?? 0;
  return `${oid ?? ""}|${line}|${process}|${seq}`;
}

/**
 * ISO 8601 문자열을 Date 로 변환 (null/undefined-safe).
 * moved 항목의 old_ / new_ 필드는 null 가능 — null 일 때 undefined 반환.
 */
function parseIso(iso: string | null | undefined): Date | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  // 왜 Number.isNaN 체크: 백엔드가 깨진 ISO 를 보내면 렌더 시 Invalid Date 가
  // 간트 x좌표 NaN 을 유발한다. fail-fast: 파싱 실패는 undefined 로 돌려 skip 렌더.
  return Number.isNaN(d.getTime()) ? undefined : d;
}

function movedEntry(t: RunCompareMovedTask): DiffIndexEntry {
  return {
    kind: "moved",
    task_id: t.task_id,
    old_start: parseIso(t.old_start),
    old_end: parseIso(t.old_end),
    old_equipment: t.old_equipment ?? undefined,
    new_start: parseIso(t.new_start),
    new_end: parseIso(t.new_end),
    new_equipment: t.new_equipment ?? undefined,
    start_delta_hours: t.start_delta_hours ?? undefined,
    equipment_changed: t.equipment_changed,
    batch_group: t.batch_group ?? null,
    process_name: t.process_name ?? null,
    sales_order_id: t.sales_order_id ?? null,
    customer_name: t.customer_name ?? null,
    sheath_color: t.sheath_color ?? null,
    cross_section: t.cross_section ?? null,
  };
}

function addedOrRemovedEntry(
  t: RunCompareAddedOrRemovedTask,
  kind: "added" | "removed",
): DiffIndexEntry {
  return {
    kind,
    task_id: t.task_id,
    batch_group: t.batch_group ?? null,
    process_name: t.process_name ?? null,
    sales_order_id: t.sales_order_id ?? null,
    customer_name: t.customer_name ?? null,
    sheath_color: t.sheath_color ?? null,
    cross_section: t.cross_section ?? null,
  };
}

/**
 * RunCompareResponse 를 task_id → DiffIndexEntry Map 으로 색인한다.
 *
 * null 입력 (compareMode OFF 또는 응답 아직 없음) 은 빈 Map 반환 — 호출부가
 * `map.get(key)` 를 undefined 로 받고 자연스럽게 overlay 없음 상태로 떨어진다.
 *
 * 성능: SchedulerView 에서 useMemo 로 감싸 diffResponse 참조가 바뀔 때만 재계산.
 * 현재 병목은 없지만 300+ 블록 규모에서도 O(N) single pass 로 수백 마이크로초 이내.
 */
export function buildDiffIndex(
  resp: RunCompareResponse | null,
): Map<string, DiffIndexEntry> {
  const map = new Map<string, DiffIndexEntry>();
  if (!resp) return map;

  for (const t of resp.moved_tasks) {
    map.set(t.task_id, movedEntry(t));
  }
  for (const t of resp.added_tasks) {
    map.set(t.task_id, addedOrRemovedEntry(t, "added"));
  }
  for (const t of resp.removed_tasks) {
    map.set(t.task_id, addedOrRemovedEntry(t, "removed"));
  }
  return map;
}
