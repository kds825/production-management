"use client";

import { XMarkIcon } from "@heroicons/react/24/outline";

import { btnPrimary, btnGhost } from "@/shared/ui/styles";
import type { BatchGroupDragModalState } from "../hooks/useBatchGroupDrag";

interface Props {
  state: BatchGroupDragModalState | null;
  onApply: () => void;
  onCancel: () => void;
}

/**
 * Task 4.2 — restore-at preview 확정 모달.
 *
 * useBatchGroupDrag.modalState 를 구독하여 task_positions 과 pushes/pulls 수를
 * 사용자에게 보여주고 '적용' / '취소' 를 유도한다.
 *
 * 배경 클릭 = 취소(onCancel), ESC 대응은 부모에서 처리하도록 위임.
 */
export function BatchGroupDragPreviewModal({
  state,
  onApply,
  onCancel,
}: Props) {
  if (!state || !state.open) return null;

  const { preview, batchGroup } = state;
  const pushCount = preview.pushes.length;
  const pullCount = preview.pulls.length;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="bg-drag-preview-title"
      className="fixed inset-0 z-[65] flex items-center justify-center bg-[color:var(--color-overlay)]"
      onClick={onCancel}
    >
      <div
        className="rounded-lg shadow-xl p-6 min-w-[440px] max-w-[560px] bg-[color:var(--color-bg-elevated)] border border-[color:var(--color-border-default)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── 헤더 ── */}
        <header className="flex items-start justify-between mb-3">
          <h3
            id="bg-drag-preview-title"
            className="text-sm font-semibold text-[color:var(--color-text-primary)]"
          >
            재배치 미리보기 — {batchGroup}
          </h3>
          <button
            type="button"
            onClick={onCancel}
            aria-label="닫기"
            className="text-[color:var(--color-text-secondary)]"
          >
            <XMarkIcon width={16} height={16} />
          </button>
        </header>

        {/* ── task_positions 목록 ── */}
        <section className="mb-3">
          <h4 className="text-xs font-semibold mb-1 text-[color:var(--color-text-primary)]">
            이 묶음의 새 위치
          </h4>
          <ul className="text-xs space-y-1">
            {preview.task_positions.map((tp) => (
              <li
                key={tp.task_id}
                className="flex gap-2 text-[color:var(--color-text-primary)]"
              >
                <span className="text-[color:var(--color-text-secondary)]">
                  {tp.process_name}
                  {tp.is_anchor ? " (앵커)" : ""}
                </span>
                <span>
                  @ {tp.new_equipment_code} / {tp.new_start}
                </span>
              </li>
            ))}
          </ul>
        </section>

        {/* ── pushes/pulls 요약 (없으면 렌더 생략) ── */}
        {(pushCount > 0 || pullCount > 0) && (
          <section className="mb-3 text-xs text-[color:var(--color-text-secondary)]">
            <p>
              다른 작업 영향: 뒤로 밀림 {pushCount}건, 당김 {pullCount}건
            </p>
          </section>
        )}

        {/* ── 푸터 버튼 ── */}
        <div className="flex justify-end gap-2">
          <button type="button" className={btnGhost} onClick={onCancel}>
            취소
          </button>
          <button type="button" className={btnPrimary} onClick={onApply}>
            적용
          </button>
        </div>
      </div>
    </div>
  );
}
