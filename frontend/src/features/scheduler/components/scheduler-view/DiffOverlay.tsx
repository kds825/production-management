/**
 * DiffOverlay — compareMode(run vs run diff) 의 "삭제된 tasks" 고스트 렌더러.
 *
 * 책임 (Single Responsibility):
 *   - 한 row(설비) 에 대해, before run 에는 있었지만 after run 에는 없는 task 를
 *     반투명 dashed 고스트로 그림. 실제 ScheduleTask 가 store 에 없으므로
 *     RunCompareAddedOrRemovedTask → ScheduleTask 합성을 여기서 수행.
 *
 * 분리 이유 (Week 7 Task 7B.2):
 *   - 기존 SchedulerView 의 GanttRow 안쪽에 인라인 JSX 로 박혀 있어 row 책임이 비대.
 *   - 합성 로직(synthesizedTask) 이 row 의 "현재 task 렌더" 와 섞여서 가독성 저하.
 *   - 분리해 둠으로써 향후 added/moved 고스트도 같은 모듈로 모이도록 확장 여지 확보.
 *
 * NOTE: added/moved 의 "현재 위치 outline" 과 "moved 의 과거 위치 ghost" 는 여전히
 * TaskRow 내부에서 렌더한다. 이는 매칭된 현재 task 와 1:1 페어링되어 있어 같은 루프에서
 * 그리는 편이 인덱싱 비용/가독성에서 우월하기 때문.
 */
"use client";

import { GanttTaskBlock } from "../GanttTaskBlock";
import { LANE_HEIGHT } from "./constants";
import type { ScheduleTask } from "../../types";
import type { RunCompareAddedOrRemovedTask } from "../../types/diff";

interface RemovedGhostsProps {
  /** 이 row 에 해당하는 설비 ID — removedTasks 중 r.equipment === equipmentId 만 그림 */
  equipmentId: string;
  rangeStart: number;
  dayWidth: number;
  weekendWidth: number;
  /** 백엔드 /runs/compare 응답의 removed_tasks 배열 */
  removedTasks: RunCompareAddedOrRemovedTask[];
}

/**
 * 한 row 에 속한 "삭제된 tasks" 를 합성해 ghost 블록으로 렌더한다.
 * 잘못된 ISO 는 fail-fast 로 skip 해 NaN x 좌표 방지.
 */
export function RemovedGhosts({
  equipmentId,
  rangeStart,
  dayWidth,
  weekendWidth,
  removedTasks,
}: RemovedGhostsProps) {
  return (
    <>
      {removedTasks
        .filter(
          (r) =>
            r.equipment === equipmentId && r.start != null && r.end != null,
        )
        .map((r) => {
          const synthStart = new Date(r.start!);
          const synthEnd = new Date(r.end!);
          if (
            Number.isNaN(synthStart.getTime()) ||
            Number.isNaN(synthEnd.getTime())
          ) {
            return null;
          }
          // 합성된 ScheduleTask 는 store 에 없으므로 lane=0 고정 (오버랩 검사 대상 외).
          const synthesizedTask: ScheduleTask = {
            id: `removed-${r.task_id}`,
            order_id: r.sales_order_id || "",
            equipment_id: r.equipment || "",
            product: r.process_name || "",
            spec: "",
            core_count: 1,
            color: "var(--color-text-tertiary)",
            start: synthStart,
            end: synthEnd,
            volume_m: 0,
            line_speed_m_per_min: 0,
            priority: "normal",
            status: "removed",
            predecessors: [],
            notes: "",
            changeover_min: 0,
            batch_group: r.batch_group || undefined,
            customer: r.customer_name || undefined,
          };
          return (
            <GanttTaskBlock
              key={synthesizedTask.id}
              task={synthesizedTask}
              rangeStart={rangeStart}
              dayWidth={dayWidth}
              weekendWidth={weekendWidth}
              lane={0}
              laneHeight={LANE_HEIGHT}
              ghost
              ghostReason="diff_removed"
            />
          );
        })}
    </>
  );
}
