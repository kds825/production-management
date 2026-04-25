/**
 * 블록 시간/설비 변경을 cascade preview + 사용자 승인 + bulk-update 흐름으로 처리하는 훅.
 *
 * 흐름 요약:
 * - FEATURE_FLAG_CASCADE_V2 off → legacy 경로(opts.legacyMove) 로 바로 위임.
 * - preview 응답이 empty (pushes/pulls/unresolved 모두 0) → 모달 skip + 바로 bulk-update.
 * - preview 에 변경이 있으면 modalState 셋업 → ConflictResolutionModal 이 구독.
 * - 모달 "적용" → pullToggle 여부에 따라 pulls 포함/제외 후 bulk-update.
 * - 422 재시도 상한 3 회: 초과 시 "수동 조정" 가이드 Toast + modalState.guidanceShown = true.
 * - 성공 시 Undo Toast (90 초) → action 으로 revertChangeSet 호출.
 *
 * 설계 노트:
 * - 프로젝트에 react-query 는 아직 도입 전 → `opts.onCommitted` 를 caller 가 수동 제공
 *   (scheduler 페이지에서 refreshTasks 재호출 등)하는 패턴.
 * - legacyMove 는 훅 호출자가 ChangeInput → 기존 store.moveTask(taskId, equipmentId, start, end)
 *   로 변환해서 넘긴다. 훅 레벨에서는 Date ↔ ISO 변환을 하지 않고 계약을 느슨하게 유지.
 */
import { useCallback, useState } from "react";
import { cascadePreview, bulkUpdate, revertChangeSet } from "../api/cascade";
import {
  BulkUpdateError,
  CascadePreviewResponse,
  TaskChange,
} from "../api/cascade.types";
import { FEATURE_FLAG_CASCADE_V2 } from "@/shared/config/featureFlags";
import { useToastStore } from "@/shared/ui/toastStore";
import { ApiError } from "@/shared/api/client";
import { useMissingReasonsStore } from "../store/missingReasonsStore";

/**
 * Week 4 Task 4B.4 — 에러로부터 X-Run-Id (apiFetch 가 ApiError 에 부여한 값) 추출.
 *
 * cascadePreview / bulkUpdate 는 fetch 직접 사용하는 별도 경로(BulkUpdateError 등) 도
 * 있어 ApiError 가 아닐 수 있다 — 그 경우 runId 는 null. 토스트 측에서 falsy 면 footer 미렌더.
 */
function extractRunId(e: unknown): string | null {
  return e instanceof ApiError ? e.runId : null;
}

/**
 * 훅의 `commit` / legacy 경로가 공유하는 최소 입력 계약.
 * new_start/new_end 는 백엔드 TZ-guard 와 일관되게 naive ISO (예: "2026-04-20T10:00:00").
 */
export interface ChangeInput {
  task_id: string;
  new_start: string;
  new_end: string;
  new_equipment_code?: string | null;
}

/**
 * 모달 (ConflictResolutionModal) 이 구독하는 상태 snapshot.
 *
 * - open: 모달 표시 여부.
 * - preview: 서버의 cascade preview 응답 전체 — 모달이 섹션별로 렌더.
 * - input: 최초 commit 시의 input (bulkUpdate 요청에 포함).
 * - guidanceShown: 422 재시도 상한을 초과해 "수동 조정" 가이드로 넘어간 상태.
 */
export interface ModalState {
  open: boolean;
  preview: CascadePreviewResponse;
  input: ChangeInput;
  guidanceShown?: boolean;
}

export interface UseScheduleChangeWithCascadeOpts {
  /**
   * FEATURE_FLAG off 시 호출되는 legacy 경로.
   * 구현체는 ChangeInput 을 기존 scheduleStore.moveTask 시그니처
   * (taskId, equipmentId, start: Date, end: Date) 로 변환해서 호출한다.
   */
  legacyMove?: (input: ChangeInput) => Promise<void> | void;
  /**
   * bulk-update / revert 성공 후 외부 state 갱신 트리거.
   * react-query invalidate 또는 scheduler 의 refreshTasks() 등.
   */
  onCommitted?: () => Promise<void> | void;
}

/**
 * 422 재시도 상한. spec: 자동 재시도는 최대 3 회, 그 이후 "수동 조정" 가이드로 전환.
 * 상수로 분리해 계약 테스트에서 참조 가능하도록 export.
 */
export const RETRY_THRESHOLD = 3;

/**
 * Undo Toast duration (ms). spec: 90 초.
 */
export const UNDO_TOAST_DURATION_MS = 90_000;

/**
 * Week 5 Task 5B.2 — 사유 입력 토스트 duration (ms). spec §8d: 60 초 sticky.
 *
 * Undo 토스트(90s) 와 별개 — 두 토스트가 동시에 노출되며, 운영자가
 * 사유 칩을 누르거나 X 로 닫거나, 60s 자동 만료될 때까지 화면 우하단에 머무른다.
 */
export const REASON_PROMPT_DURATION_MS = 60_000;

export function useScheduleChangeWithCascade(
  opts: UseScheduleChangeWithCascadeOpts = {},
) {
  // show 만 selector 로 뽑아 불필요한 rerender 방지.
  const showToast = useToastStore((s) => s.show);
  // Week 5 Task 5B.2 — 사유 토스트 닫힘/성공 시 헤더 배지 카운트 push refresh.
  const refreshMissingReasons = useMissingReasonsStore((s) => s.refresh);

  const [modalState, setModalState] = useState<ModalState | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  // Pull 제안 포함 여부 — 기본 on (사용자가 토글로 off 가능).
  const [pullToggle, setPullToggle] = useState(true);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

  const reset = useCallback(() => {
    setModalState(null);
    setRetryCount(0);
    setPullToggle(true);
  }, []);

  const showUndoToast = useCallback(
    (changeSetId: string) => {
      showToast("적용 완료", "success", UNDO_TOAST_DURATION_MS, {
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
              undefined,
              undefined,
              { runId: extractRunId(e) },
            );
          }
        },
      });
    },
    [opts, showToast],
  );

  /**
   * Week 5 Task 5B.2 — 드래그-드롭 직후 사유 입력 토스트.
   *
   * - duration 60s sticky.
   * - changeSetId 가 비어 있으면(no-op 변경 등) 토스트 생략.
   * - Undo 토스트와 병렬 노출 — 운영자가 둘 다 처리 가능하도록 설계.
   * - kind="reason-prompt" → Toast.tsx 가 칩 4종 렌더 + PATCH 호출.
   * - 칩 클릭/X/자동 만료 시 onResolved 가 호출되어 헤더 배지 갱신.
   *   (성공 시: 1건 감소 / 스킵 시: count 그대로지만 push refresh 로 캐시 동기화)
   */
  const showReasonToast = useCallback(
    (changeSetId: string) => {
      if (!changeSetId) return;
      // 즉시 배지 1 증가 효과 — 사유가 미기록 상태로 새 change_set 이 추가됐으므로
      // 사용자가 토스트를 닫기 전에라도 헤더 카운트에 반영.
      void refreshMissingReasons();
      showToast(
        "사유를 선택해주세요. (60초 후 자동 닫힘)",
        "info",
        REASON_PROMPT_DURATION_MS,
        undefined,
        {
          kind: "reason-prompt",
          changeSetId,
          onResolved: () => {
            void refreshMissingReasons();
          },
        },
      );
    },
    [refreshMissingReasons, showToast],
  );

  /**
   * 블록 편집 진입점.
   * - flag off → legacyMove 위임.
   * - flag on  → preview 호출 → empty 면 즉시 bulk-update, 아니면 modal 셋업.
   */
  const commit = useCallback(
    async (input: ChangeInput) => {
      if (!FEATURE_FLAG_CASCADE_V2) {
        if (opts.legacyMove) await opts.legacyMove(input);
        return;
      }
      setIsPreviewLoading(true);
      try {
        const preview = await cascadePreview(input);
        const totalChanges =
          preview.pushes.length +
          preview.pulls.length +
          preview.unresolved.length;
        if (totalChanges === 0) {
          // preview 가 비어 있으면 사용자 승인 불필요 → 즉시 커밋.
          const { change_set_id } = await bulkUpdate({
            changes: [input],
            expected_cascade_request_id: preview.request_id,
          });
          await opts.onCommitted?.();
          // Undo + 사유 입력 토스트 병렬 노출 (각각 90s / 60s sticky).
          showUndoToast(change_set_id);
          showReasonToast(change_set_id);
          return;
        }
        setModalState({ open: true, preview, input });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        // Week 4 Task 4B.4 — runId 가 있으면 toast 가 footer 에 코릴레이션 ID 노출.
        // Week 5 Task 5B.2 spec §8d: 백엔드가 드래그를 거부하면 토스트는 "이동 실패 —
        // 사유가 저장되지 않았습니다." 로 표기 (사유 칩이 뜨지 않을 것임을 명시).
        showToast(
          `이동 실패 — 사유가 저장되지 않았습니다. (${msg})`,
          "error",
          undefined,
          undefined,
          { runId: extractRunId(e) },
        );
      } finally {
        setIsPreviewLoading(false);
      }
    },
    [opts, showToast, showUndoToast, showReasonToast],
  );

  /**
   * 모달 "적용" 버튼 핸들러.
   * pullToggle=false 면 preview.pulls 를 changes 에서 제외.
   * 422 재시도 상한 초과 시 modalState.guidanceShown 으로 전환 — 모달이
   * "수동 조정으로 이동" 문구를 표시할 수 있도록.
   */
  const applyModal = useCallback(async () => {
    if (!modalState) return;
    const { preview, input } = modalState;
    const changes: TaskChange[] = [
      ...preview.pushes.map((p) => ({
        task_id: p.task_id,
        new_start: p.new_start,
        new_end: p.new_end,
      })),
      ...(pullToggle
        ? preview.pulls.map((p) => ({
            task_id: p.task_id,
            new_start: p.new_start,
            new_end: p.new_end,
          }))
        : []),
      input,
    ];
    try {
      const { change_set_id } = await bulkUpdate({
        changes,
        expected_cascade_request_id: preview.request_id,
      });
      await opts.onCommitted?.();
      showUndoToast(change_set_id);
      showReasonToast(change_set_id);
      reset();
    } catch (e) {
      if (e instanceof BulkUpdateError) {
        const next = retryCount + 1;
        if (next >= RETRY_THRESHOLD) {
          showToast(
            "자동 해소 불가. 수동 조정으로 이동해주세요.",
            "error",
            8000,
          );
          setModalState((s) => (s ? { ...s, guidanceShown: true } : s));
          return;
        }
        setRetryCount(next);
        showToast(
          `재적용 실패 (${next}/${RETRY_THRESHOLD}): ${e.code}`,
          "error",
          5000,
        );
        return;
      }
      // Week 5 Task 5B.2 spec §8d — 드래그 거부 시 사유 입력 토스트 미발생을
      // 사용자가 즉시 인지하도록 명시적 메시지.
      showToast(
        `이동 실패 — 사유가 저장되지 않았습니다. (${e instanceof Error ? e.message : String(e)})`,
        "error",
        undefined,
        undefined,
        { runId: extractRunId(e) },
      );
    }
  }, [
    modalState,
    pullToggle,
    retryCount,
    opts,
    showToast,
    showUndoToast,
    showReasonToast,
    reset,
  ]);

  const closeModal = useCallback(() => reset(), [reset]);

  return {
    commit,
    applyModal,
    closeModal,
    modalState,
    setModalState,
    pullToggle,
    setPullToggle,
    retryCount,
    isPreviewLoading,
  };
}
