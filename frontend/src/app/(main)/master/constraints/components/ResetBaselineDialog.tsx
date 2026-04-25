"use client";

/**
 * Week 5 Task 5B.1 — 임의 베이스라인으로 ConstraintConfig 리셋하는 확인 다이얼로그.
 *
 * Step 1: 사용자가 BaselineTab 의 드롭다운에서 선택한 베이스라인 vs 현재 상태의
 *         diff preview. (PromoteBaselineDialog 와 동일한 클라이언트사이드 합성.)
 * Step 2: confirm. 423 → 솔버 락 토스트, 200 → 성공 토스트 + 부모 refresh.
 *
 * 백엔드 reset 엔드포인트는 changed_by 만 받아 단순하므로 여기서는 form input 없이
 * 확인만 받는다.
 */

import { useEffect, useState } from "react";
import { XMarkIcon } from "@heroicons/react/24/outline";
import {
  VersionDiff,
  type DiffRow,
} from "@/features/constraints/components/VersionDiff";
import { useToastStore } from "@/shared/ui/toastStore";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface ConstraintRow {
  constraint_id: string;
  params_json: Record<string, unknown> | null;
}

interface HistoryRow {
  history_id: number;
  changed_at: string;
  changed_by: string | null;
  old_params_json: Record<string, unknown> | null;
  new_params_json: Record<string, unknown>;
}

interface ResetBaselineDialogProps {
  open: boolean;
  changedBy: string; // 선택된 베이스라인의 full changed_by
  tag: string; // 사용자에게 노출할 짧은 라벨
  onClose: () => void;
  onReset: () => void; // 성공 시 부모가 constraints 재조회하도록 알림
}

function buildDiff(
  current: Record<string, unknown>,
  baseline: Record<string, unknown>,
  constraintId: string,
): DiffRow[] {
  const rows: DiffRow[] = [];
  const keys = new Set([...Object.keys(current), ...Object.keys(baseline)]);
  for (const k of Array.from(keys).sort()) {
    const va = baseline[k];
    const vb = current[k];
    if (JSON.stringify(va) !== JSON.stringify(vb)) {
      rows.push({
        constraint_id: constraintId,
        field: k,
        value_a: va ?? null,
        value_b: vb ?? null,
      });
    }
  }
  return rows;
}

export function ResetBaselineDialog({
  open,
  changedBy,
  tag,
  onClose,
  onReset,
}: ResetBaselineDialogProps) {
  const showToast = useToastStore((s) => s.show);

  const [submitting, setSubmitting] = useState(false);
  const [diff, setDiff] = useState<{ rows: DiffRow[] } | "loading" | "error">(
    "loading",
  );

  useEffect(() => {
    if (!open) return;
    setSubmitting(false);
    setDiff("loading");
    let cancelled = false;

    const loadDiff = async () => {
      try {
        const cResp = await fetch(`${API}/constraints`);
        if (!cResp.ok) throw new Error(`current constraints ${cResp.status}`);
        const cData = await cResp.json();
        const current: ConstraintRow[] = cData.constraints || [];

        const baselineParamsByCid: Record<string, Record<string, unknown>> = {};
        for (const row of current) {
          try {
            const hResp = await fetch(
              `${API}/constraints/${encodeURIComponent(row.constraint_id)}/history`,
            );
            if (!hResp.ok) continue;
            const hData = await hResp.json();
            const matched = (hData.history as HistoryRow[] | undefined)?.find(
              (h) => h.changed_by === changedBy,
            );
            if (matched) {
              baselineParamsByCid[row.constraint_id] =
                matched.new_params_json ?? {};
            }
          } catch {
            // skip — 단일 constraint 조회 실패 비치명적.
          }
        }

        const allRows: DiffRow[] = [];
        for (const row of current) {
          const baselineParams = baselineParamsByCid[row.constraint_id] ?? {};
          const currentParams = (row.params_json ?? {}) as Record<
            string,
            unknown
          >;
          allRows.push(
            ...buildDiff(currentParams, baselineParams, row.constraint_id),
          );
        }

        if (!cancelled) setDiff({ rows: allRows });
      } catch (e) {
        if (!cancelled) {
          setDiff("error");
          console.error("[ResetBaselineDialog] diff load failed", e);
        }
      }
    };

    void loadDiff();
    return () => {
      cancelled = true;
    };
  }, [open, changedBy]);

  if (!open) return null;

  const handleReset = async () => {
    setSubmitting(true);
    try {
      const resp = await fetch(`${API}/constraints/reset-to-baseline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changed_by: changedBy }),
      });

      if (resp.status === 423) {
        showToast(
          "솔버 실행 중에는 베이스라인을 수정할 수 없습니다.",
          "warning",
        );
        return;
      }

      if (!resp.ok) {
        const detail = await resp
          .json()
          .then((d) => d.detail)
          .catch(() => null);
        showToast(
          `리셋 실패 (${resp.status}): ${detail ?? "unknown"}`,
          "error",
        );
        return;
      }

      const data = await resp.json();
      showToast(
        `'${tag}' 베이스라인으로 리셋되었습니다 (${data.reset_count}개 제약).`,
        "success",
      );
      onReset();
      onClose();
    } catch (e) {
      showToast(e instanceof Error ? e.message : "리셋 실패", "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="reset-dialog-title"
      className="fixed inset-0 z-[60] flex items-center justify-center bg-[color:var(--color-overlay)] p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-lg bg-[color:var(--color-bg-elevated)] p-6 shadow-xl border border-[color:var(--color-border-default)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-4 flex items-center justify-between">
          <h3
            id="reset-dialog-title"
            className="text-base font-semibold text-[color:var(--color-text-primary)]"
          >
            베이스라인으로 리셋: {tag}
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="text-[color:var(--color-text-secondary)]"
          >
            <XMarkIcon width={16} height={16} />
          </button>
        </header>

        <p className="mb-3 text-xs text-[color:var(--color-text-secondary)]">
          현재 ConstraintConfig 가 선택된 베이스라인 시점 값으로 덮어써집니다.
          진행 중인 솔버 실행이 있으면 거부됩니다.
        </p>

        {diff === "loading" && (
          <div className="rounded border border-dashed border-[color:var(--color-border-default)] p-6 text-center text-xs text-[color:var(--color-text-secondary)]">
            diff 계산 중...
          </div>
        )}
        {diff === "error" && (
          <div className="rounded border border-[color:var(--color-danger)] bg-[color:var(--color-bg-muted)] px-3 py-2 text-xs text-[color:var(--color-danger)]">
            diff 계산 실패. 그래도 리셋을 진행할 수 있습니다.
          </div>
        )}
        {typeof diff === "object" && diff !== null && (
          <>
            <div className="mb-2 text-xs text-[color:var(--color-text-secondary)]">
              변경 항목: {diff.rows.length}개 (베이스라인 ↔ 현재)
            </div>
            <VersionDiff rows={diff.rows} labelA={tag} labelB="현재" />
          </>
        )}

        <footer className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="px-3 py-1.5 text-xs font-medium rounded transition-colors text-[color:var(--color-text-primary)] hover:bg-[color:var(--color-bg-muted)] disabled:opacity-50"
          >
            취소
          </button>
          <button
            type="button"
            onClick={() => void handleReset()}
            disabled={submitting}
            className="px-3 py-1.5 text-xs font-medium rounded transition-opacity text-[color:var(--color-text-inverse)] bg-[color:var(--color-brand-primary)] hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? "리셋 중..." : "리셋 실행"}
          </button>
        </footer>
      </div>
    </div>
  );
}
