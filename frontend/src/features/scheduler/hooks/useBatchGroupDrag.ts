/**
 * batch_group 드래그-드롭 orchestration.
 *
 * Flow Y (restore/reassign):
 *   BatchGroupCard → equipment-row 드롭
 *   - isOrigin=true → useScheduleStore.restoreBatchGroup(bg) 직접 호출 (cascade-preview 생략)
 *   - isOrigin=false → restoreBatchGroupAt preview → 사용자 확정 시 bulkUpdate + 90s Undo
 *
 * 설계:
 *   - useScheduleChangeWithCascade.showUndoToast 패턴 재사용 (UNDO_TOAST_DURATION_MS 상수 공유).
 *   - unresolved reason 매핑은 reasonMap 으로 — 백엔드 enum 추가 시 여기에 항목 추가.
 */
import { useCallback, useState } from "react";
import {
  restoreBatchGroupAt,
  bulkUpdate,
  revertChangeSet,
} from "../api/cascade";
import type { RestoreAtResponse, TaskChange } from "../api/cascade.types";
import { useScheduleStore } from "../store/scheduleStore";
import { useToastStore } from "@/shared/ui/toastStore";
import { UNDO_TOAST_DURATION_MS } from "./useScheduleChangeWithCascade";

export interface BatchGroupDropTarget {
  equipmentCode: string;
  anchorStart: Date;
  /** 원위치 판정 — 프론트에서 배치 묶음의 원 anchor 위치와 비교해 결정. */
  isOrigin: boolean;
}

export interface BatchGroupDragModalState {
  open: boolean;
  preview: RestoreAtResponse;
  batchGroup: string;
}

export interface UseBatchGroupDragOpts {
  onCommitted?: () => Promise<void> | void;
}

/** 로컬 Date → naive ISO (백엔드 KST 계약, Z 접미사 없음). */
function toIsoNaive(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

export function useBatchGroupDrag(opts: UseBatchGroupDragOpts = {}) {
  const [modalState, setModalState] = useState<BatchGroupDragModalState | null>(
    null,
  );
  const showToast = useToastStore((s) => s.show);

  const showUndoToast = useCallback(
    (changeSetId: string) => {
      showToast("재배치 적용 완료", "success", UNDO_TOAST_DURATION_MS, {
        label: "되돌리기",
        onClick: async () => {
          try {
            await revertChangeSet(changeSetId);
            await opts.onCommitted?.();
            showToast("되돌렸습니다.", "success");
          } catch (e) {
            showToast(
              `되돌리기 실패: ${e instanceof Error ? e.message : String(e)}`,
              "error",
            );
          }
        },
      });
    },
    [opts, showToast],
  );

  const onDropToEquipment = useCallback(
    async (batchGroup: string, target: BatchGroupDropTarget) => {
      if (target.isOrigin) {
        await useScheduleStore.getState().restoreBatchGroup(batchGroup);
        return;
      }

      try {
        const preview = await restoreBatchGroupAt(batchGroup, {
          anchor_equipment_code: target.equipmentCode,
          anchor_start: toIsoNaive(target.anchorStart),
        });

        if (preview.unresolved.length > 0) {
          const first = preview.unresolved[0];
          const reasonMap: Record<string, string> = {
            invalid_equipment: "선택한 설비가 공정 경로와 맞지 않습니다",
            due_date_violation: "납기 초과로 자동 배치 불가",
            no_space_forward: "앞쪽 빈 공간 부족",
            cycle_detected: "순환 종속성 감지",
          };
          const msg = reasonMap[first.reason] ?? `재배치 불가: ${first.reason}`;
          showToast(msg, "warning", 6000);
          return;
        }

        setModalState({ open: true, preview, batchGroup });
      } catch (e) {
        showToast(
          `재배치 계산 실패: ${e instanceof Error ? e.message : String(e)}`,
          "error",
        );
      }
    },
    [showToast],
  );

  const applyModal = useCallback(async () => {
    if (!modalState) return;
    const { preview, batchGroup } = modalState;

    const changes: TaskChange[] = [
      ...preview.task_positions.map((tp) => ({
        task_id: `TASK-${tp.task_id}`,
        new_start: tp.new_start,
        new_end: tp.new_end,
        new_equipment_code: tp.new_equipment_code,
      })),
      ...preview.pushes.map((p) => ({
        task_id: p.task_id,
        new_start: p.new_start,
        new_end: p.new_end,
      })),
      ...preview.pulls.map((p) => ({
        task_id: p.task_id,
        new_start: p.new_start,
        new_end: p.new_end,
      })),
    ];

    try {
      const { change_set_id } = await bulkUpdate({
        changes,
        expected_cascade_request_id: preview.request_id,
      });
      useScheduleStore.setState((s) => ({
        unscheduledItems: s.unscheduledItems.filter(
          (i) =>
            !(i.kind === "batch_group" && i.group.batch_group === batchGroup),
        ),
      }));
      await opts.onCommitted?.();
      showUndoToast(change_set_id);
      setModalState(null);
    } catch (e) {
      showToast(
        `재배치 적용 실패: ${e instanceof Error ? e.message : String(e)}`,
        "error",
      );
    }
  }, [modalState, opts, showToast, showUndoToast]);

  const cancelModal = useCallback(() => {
    setModalState(null);
  }, []);

  return {
    modalState,
    onDropToEquipment,
    applyModal,
    cancelModal,
  };
}
