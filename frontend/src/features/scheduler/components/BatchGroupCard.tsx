"use client";

import { Link2 } from "lucide-react";
import { ChevronRightIcon } from "@heroicons/react/24/outline";
import { useDraggable } from "@dnd-kit/core";

import { btnPrimary, chipMuted, chipReason } from "@/shared/ui/styles";
import type { BatchGroupSnapshot } from "../types";
import { useScheduleStore } from "../store/scheduleStore";

interface Props {
  group: BatchGroupSnapshot;
}

// 납기 표시는 M/D 형식이 가장 정보 밀도가 낮고 카드 폭(200px)에 맞음.
function formatDeliveryDate(iso: string): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function BatchGroupCard({ group }: Props) {
  const restoreBatchGroup = useScheduleStore((s) => s.restoreBatchGroup);
  // Set.has 는 참조-안정한 Set에서 안전. 스토어가 immer + enableMapSet로 set 교체 시 리렌더됨.
  const inFlight = useScheduleStore((s) =>
    s.inFlightBatchGroups.has(group.batch_group),
  );

  // Task 3.2: WIP 매칭 검사 — snapshot 에는 task-level 정보가 없으므로
  // store.tasks 에서 같은 batch_group 의 task 중 wip_matched_id 가 있으면 드래그 차단.
  const wipMatched = useScheduleStore((s) =>
    s.tasks.some(
      (t) => t.batch_group === group.batch_group && t.wip_matched_id != null,
    ),
  );

  const disabled = inFlight || wipMatched;

  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `bg-${group.batch_group}`,
    disabled,
    data: {
      type: "batch_group",
      group,
    },
  });

  const firstProcess = group.processes[0];
  // 백엔드가 process_name 을 equipment_group으로 내려보내므로 CSS var 매치가 안 될 수 있음.
  // var(x, fallback) 구문으로 graceful 처리.
  const equipmentGroup = firstProcess?.equipment_group ?? "연선";

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      role="group"
      aria-label={`배치 묶음 ${group.batch_group}, ${group.order_count}수주, 사유: ${group.unassign_reason}`}
      tabIndex={0}
      className="p-2.5 rounded-lg flex-shrink-0 bg-[color:var(--color-bg-elevated)] border-[1.5px] select-none"
      style={{
        width: 200,
        borderColor: `var(--color-process-${equipmentGroup}, var(--color-border-default))`,
        opacity: isDragging ? 0.5 : 1,
        cursor: disabled ? "not-allowed" : isDragging ? "grabbing" : "grab",
      }}
      title={`총 길이: ${group.total_length_m.toLocaleString()} m`}
    >
      {/* 헤더: 수주 수 + 사유 배지 */}
      <div className="flex items-start justify-between gap-1 mb-1">
        <span className="text-tiny font-semibold flex items-center gap-1 text-[color:var(--color-text-primary)]">
          <Link2 width={12} height={12} aria-hidden />
          {group.order_count}수주
        </span>
        <span className={chipReason} title="미배정 사유">
          {group.unassign_reason}
        </span>
      </div>

      {/* 설비 그룹 + 규격 */}
      <div className="flex items-center gap-1 mb-0.5">
        <span className={chipMuted}>{equipmentGroup}</span>
      </div>
      <div className="text-tiny truncate mb-0.5 text-[color:var(--color-text-secondary)]">
        {group.spec || "–"}
        {group.color ? ` · ${group.color}` : ""}
      </div>
      <div className="text-tiny truncate mb-1 text-[color:var(--color-text-tertiary)]">
        {group.customer || "고객 미상"}
      </div>

      {/* 공정체인 */}
      <div className="flex items-center gap-1 py-1 border-t border-[color:var(--color-border-muted)]">
        {group.processes.map((p, i) => (
          <div key={`${p.process}-${i}`} className="flex items-center gap-0.5">
            <span className={chipMuted}>{p.process}</span>
            {i < group.processes.length - 1 && (
              <ChevronRightIcon
                width={10}
                height={10}
                aria-hidden
                className="text-[color:var(--color-text-tertiary)]"
              />
            )}
          </div>
        ))}
      </div>

      {/* 납기 */}
      <div className="flex items-center justify-between mt-1 pt-1 border-t border-[color:var(--color-border-muted)]">
        <span className="text-mini text-[color:var(--color-text-tertiary)]">
          납기 {formatDeliveryDate(group.delivery_date)}
        </span>
      </div>

      {/* 복원 버튼 */}
      <button
        type="button"
        className={`${btnPrimary} w-full mt-1.5`}
        disabled={inFlight}
        aria-label="묶음을 원래 자리로 복원"
        onClick={() => {
          void restoreBatchGroup(group.batch_group);
        }}
      >
        {inFlight ? "복원 중..." : "계획으로 복원"}
      </button>
    </div>
  );
}
