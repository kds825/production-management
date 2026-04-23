"use client";

import { useState } from "react";
import { XMarkIcon } from "@heroicons/react/24/outline";

import { btnPrimary, btnGhost } from "@/shared/ui/styles";
import {
  UNASSIGN_REASONS,
  type UnassignReason,
  type ScheduleTask,
} from "../types";

interface Props {
  isOpen: boolean;
  batchGroup: string;
  tasks: ScheduleTask[];
  onConfirm: (reason: UnassignReason, dontAskAgain: boolean) => void;
  onCancel: () => void;
}

export function UnassignConfirmModal({
  isOpen,
  batchGroup,
  tasks,
  onConfirm,
  onCancel,
}: Props) {
  const [reason, setReason] = useState<UnassignReason>("자재지연");
  const [dontAskAgain, setDontAskAgain] = useState(false);

  if (!isOpen) return null;

  // tasks → 공정 문자열 / 수주 수
  const processes = Array.from(
    new Set(tasks.map((t) => t.product || "").filter(Boolean)),
  ).join(" → ");
  const orderCount = new Set(tasks.map((t) => t.order_id)).size;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="unassign-dialog-title"
      className="fixed inset-0 z-[60] flex items-center justify-center bg-[color:var(--color-overlay)]"
      onClick={onCancel}
    >
      <div
        className="rounded-lg shadow-xl p-6 min-w-[400px] max-w-[520px] bg-[color:var(--color-bg-elevated)] border border-[color:var(--color-border-default)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between mb-3">
          <h3
            id="unassign-dialog-title"
            className="text-sm font-semibold text-[color:var(--color-text-primary)]"
          >
            이 묶음을 미배정 작업으로 이동합니다
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

        <dl className="text-xs mb-3 space-y-1">
          <div className="flex gap-2">
            <dt className="text-[color:var(--color-text-secondary)]">
              포함 공정:
            </dt>
            <dd className="text-[color:var(--color-text-primary)]">
              {processes || "–"}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-[color:var(--color-text-secondary)]">
              포함 수주:
            </dt>
            <dd className="text-[color:var(--color-text-primary)]">
              {orderCount}건
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-[color:var(--color-text-secondary)]">
              batch_group:
            </dt>
            <dd className="text-[color:var(--color-text-tertiary)] font-mono">
              {batchGroup}
            </dd>
          </div>
        </dl>

        <fieldset className="mb-4">
          <legend className="text-xs font-semibold mb-2 text-[color:var(--color-text-primary)]">
            이동 사유
          </legend>
          <div className="flex flex-col gap-1">
            {UNASSIGN_REASONS.map((r) => (
              <label
                key={r}
                className="flex items-center gap-2 text-xs cursor-pointer"
              >
                <input
                  type="radio"
                  name="reason"
                  value={r}
                  checked={reason === r}
                  onChange={() => setReason(r)}
                  className="accent-[color:var(--color-brand-primary)]"
                />
                <span className="text-[color:var(--color-text-primary)]">
                  {r}
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <p className="text-xs mb-3 text-[color:var(--color-text-secondary)]">
          미배정으로 이동 후 "계획으로 복원" 버튼으로 언제든 원래 자리로 돌릴 수
          있습니다.
        </p>

        <label className="flex items-center gap-2 text-xs mb-4 cursor-pointer">
          <input
            type="checkbox"
            checked={dontAskAgain}
            onChange={(e) => setDontAskAgain(e.target.checked)}
            className="accent-[color:var(--color-brand-primary)]"
          />
          <span className="text-[color:var(--color-text-secondary)]">
            이 세션에서 다시 묻지 않기
          </span>
        </label>

        <div className="flex justify-end gap-2">
          <button type="button" className={btnGhost} onClick={onCancel}>
            취소
          </button>
          <button
            type="button"
            className={btnPrimary}
            onClick={() => onConfirm(reason, dontAskAgain)}
          >
            미배정으로 이동
          </button>
        </div>
      </div>
    </div>
  );
}
