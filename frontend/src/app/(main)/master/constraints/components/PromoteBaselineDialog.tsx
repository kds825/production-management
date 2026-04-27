"use client";

/**
 * Week 5 Task 5B.1 — 현재 제약 상태를 새 베이스라인으로 승격하는 2단계 다이얼로그.
 *
 * Step 1: 직전 베이스라인 vs 현재 ConstraintConfig 의 diff preview.
 *         (백엔드의 단일 GET 엔드포인트가 없어 클라이언트 사이드에서 합성:
 *          /api/constraints + /api/constraints/baselines + /api/constraints/{id}/history
 *          를 결합해 베이스라인 시점 params 를 복원한 후 현재값과 비교.)
 * Step 2: tag / 승인 근거(approval_note) 입력 폼. 두 필드 모두 채워야 confirm 가능.
 *
 * 423 (솔버 실행 중) 응답을 토스트로 안내 — 백엔드가 promote 호출 시 유일하게
 * 차단하는 케이스이므로 이 메시지가 운영자에게 명확히 전달되어야 한다.
 */

import { useEffect, useMemo, useState } from "react";
import { XMarkIcon } from "@heroicons/react/24/outline";
import {
  VersionDiff,
  type DiffRow,
} from "@/features/constraints/components/VersionDiff";
import { useToastStore } from "@/shared/ui/toastStore";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

// Backend list_baselines 응답 row 형태와 1:1.
interface Baseline {
  tag: string;
  changed_by: string;
  created_at: string | null;
  row_count: number;
}

interface ConstraintRow {
  constraint_id: string;
  params_json: Record<string, unknown> | null;
}

// /constraints/{id}/history 응답 row.
interface HistoryRow {
  history_id: number;
  changed_at: string;
  changed_by: string | null;
  old_params_json: Record<string, unknown> | null;
  new_params_json: Record<string, unknown>;
}

interface PromoteBaselineDialogProps {
  open: boolean;
  baselines: Baseline[]; // BaselineTab 이 이미 fetch 했으므로 재사용 (네트워크 절약)
  onClose: () => void;
  onPromoted: () => void; // 성공 시 BaselineTab 이 list refetch 하도록 알림
}

type Step = "diff" | "form";

interface DiffData {
  rows: DiffRow[];
  baseLabel: string; // diff 표 좌측 라벨 — 직전 베이스라인 tag
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

/** 기본 tag 후보: 오늘 날짜·시:분 + 사용자 이니셜 슬롯. 사용자가 자유롭게 수정 가능. */
function defaultTagCandidate(initials: string): string {
  const now = new Date();
  const date = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
  const time = `${pad2(now.getHours())}${pad2(now.getMinutes())}`;
  const tail = initials ? `-${initials}` : "";
  return `${date}-${time}${tail}`;
}

/** baselines 배열에서 created_at 이 가장 최신인 것을 골라 반환. */
function pickLatestBaseline(baselines: Baseline[]): Baseline | null {
  if (baselines.length === 0) return null;
  // list_baselines 가 이미 created_at DESC 로 정렬해 보내지만, defensive copy.
  const sorted = [...baselines].sort((a, b) => {
    const ta = a.created_at ?? "";
    const tb = b.created_at ?? "";
    return tb.localeCompare(ta);
  });
  return sorted[0];
}

/** 두 객체의 키 단위 diff 를 DiffRow[] 로 변환. */
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

export function PromoteBaselineDialog({
  open,
  baselines,
  onClose,
  onPromoted,
}: PromoteBaselineDialogProps) {
  const showToast = useToastStore((s) => s.show);

  const [step, setStep] = useState<Step>("diff");
  const [tag, setTag] = useState("");
  const [createdBy, setCreatedBy] = useState("");
  const [approvalNote, setApprovalNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [diff, setDiff] = useState<DiffData | "loading" | "error" | "no-base">(
    "loading",
  );

  // open 전이 시 상태 초기화 — modal 재오픈마다 깨끗한 상태에서 시작.
  useEffect(() => {
    if (!open) return;
    setStep("diff");
    setTag(defaultTagCandidate(""));
    setCreatedBy("");
    setApprovalNote("");
    setSubmitting(false);
    setDiff("loading");
  }, [open]);

  // 사용자가 createdBy 를 입력하면 tag 후보에 자동 합성 — 단, 사용자가 직접
  // tag 를 수정한 흔적이 있으면 (디폴트와 다르면) 덮어쓰지 않는다.
  useEffect(() => {
    if (!open) return;
    setTag((prev) => {
      // prev 가 직전 default candidate 와 같을 때만 갱신 (사용자 수정 보존).
      const prevWithoutInitials = prev.replace(/-[A-Za-z]+$/, "");
      const expectedBase = defaultTagCandidate("");
      if (prev === expectedBase || prevWithoutInitials === expectedBase) {
        return defaultTagCandidate(createdBy.trim());
      }
      return prev;
    });
  }, [createdBy, open]);

  // Step 1 데이터 로드 — open 시 1회.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    const loadDiff = async () => {
      try {
        // 1) 현재 ConstraintConfig 전체.
        const cResp = await fetch(`${API}/constraints`);
        if (!cResp.ok) throw new Error(`current constraints ${cResp.status}`);
        const cData = await cResp.json();
        const current: ConstraintRow[] = cData.constraints || [];

        // 2) 직전 baseline 결정.
        const latest = pickLatestBaseline(baselines);
        if (!latest) {
          if (!cancelled) setDiff("no-base");
          return;
        }

        // 3) 각 constraint 의 history 에서 latest.changed_by 매칭 행을 골라
        //    new_params_json 을 베이스라인 시점 params 로 복원.
        //    Why: GET /baselines 는 row_count 만 주고 실제 params 는 history 에 있음.
        //    소수 ConstraintConfig (수십개) 라 N+1 비용 무시 가능.
        const baselineParamsByCid: Record<string, Record<string, unknown>> = {};
        for (const row of current) {
          try {
            const hResp = await fetch(
              `${API}/constraints/${encodeURIComponent(row.constraint_id)}/history`,
            );
            if (!hResp.ok) continue;
            const hData = await hResp.json();
            const matched = (hData.history as HistoryRow[] | undefined)?.find(
              (h) => h.changed_by === latest.changed_by,
            );
            if (matched) {
              baselineParamsByCid[row.constraint_id] =
                matched.new_params_json ?? {};
            }
          } catch {
            // 단일 constraint 의 history 조회 실패는 비치명적 — 해당 행만 누락.
          }
        }

        // 4) constraint_id 별 diff 합치기.
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

        if (!cancelled) {
          setDiff({ rows: allRows, baseLabel: latest.tag });
        }
      } catch (e) {
        if (!cancelled) {
          setDiff("error");
          console.error("[PromoteBaselineDialog] diff load failed", e);
        }
      }
    };

    void loadDiff();
    return () => {
      cancelled = true;
    };
  }, [open, baselines]);

  const canSubmit = useMemo(() => {
    return (
      tag.trim().length > 0 &&
      createdBy.trim().length > 0 &&
      approvalNote.trim().length > 0 &&
      !submitting
    );
  }, [tag, createdBy, approvalNote, submitting]);

  if (!open) return null;

  const handlePromote = async () => {
    setSubmitting(true);
    try {
      const resp = await fetch(`${API}/constraints/promote-baseline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tag: tag.trim(),
          created_by: createdBy.trim(),
          approval_note: approvalNote.trim(),
        }),
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
          `베이스라인 승격 실패 (${resp.status}): ${detail ?? "unknown"}`,
          "error",
        );
        return;
      }

      const data = await resp.json();
      showToast(
        `'${data.tag}' 베이스라인이 생성되었습니다 (${data.row_count}개 제약).`,
        "success",
      );
      onPromoted();
      onClose();
    } catch (e) {
      showToast(
        e instanceof Error ? e.message : "베이스라인 승격 실패",
        "error",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="promote-dialog-title"
      className="fixed inset-0 z-[60] flex items-center justify-center bg-[color:var(--color-overlay)] p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-lg bg-[color:var(--color-bg-elevated)] p-6 shadow-xl border border-[color:var(--color-border-default)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-4 flex items-center justify-between">
          <h3
            id="promote-dialog-title"
            className="text-base font-semibold text-[color:var(--color-text-primary)]"
          >
            새 베이스라인 생성 · 단계 {step === "diff" ? "1" : "2"}/2
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

        {step === "diff" && (
          <section>
            <p className="mb-3 text-xs text-[color:var(--color-text-secondary)]">
              직전 베이스라인과 현재 제약 상태의 차이를 확인하세요. 차이가 없는
              경우에도 새 스냅샷을 생성할 수 있습니다.
            </p>
            {diff === "loading" && (
              <div className="rounded border border-dashed border-[color:var(--color-border-default)] p-6 text-center text-xs text-[color:var(--color-text-secondary)]">
                diff 계산 중...
              </div>
            )}
            {diff === "error" && (
              <div className="rounded border border-[color:var(--color-danger)] bg-[color:var(--color-bg-muted)] px-3 py-2 text-xs text-[color:var(--color-danger)]">
                diff 계산 실패. 그래도 다음 단계로 진행할 수 있습니다.
              </div>
            )}
            {diff === "no-base" && (
              <div className="rounded border border-dashed border-[color:var(--color-border-default)] p-6 text-center text-xs text-[color:var(--color-text-secondary)]">
                기존 베이스라인이 없습니다. 이번이 첫 번째 스냅샷이 됩니다.
              </div>
            )}
            {typeof diff === "object" && diff !== null && (
              <>
                <div className="mb-2 text-xs text-[color:var(--color-text-secondary)]">
                  변경 항목: {diff.rows.length}개
                </div>
                <VersionDiff
                  rows={diff.rows}
                  labelA={diff.baseLabel}
                  labelB="현재"
                />
              </>
            )}

            <footer className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1.5 text-xs font-medium rounded transition-colors text-[color:var(--color-text-primary)] hover:bg-[color:var(--color-bg-muted)]"
              >
                취소
              </button>
              <button
                type="button"
                onClick={() => setStep("form")}
                className="px-3 py-1.5 text-xs font-medium rounded transition-opacity text-[color:var(--color-text-inverse)] bg-[color:var(--color-brand-primary)] hover:opacity-90"
              >
                다음 단계
              </button>
            </footer>
          </section>
        )}

        {step === "form" && (
          <section>
            <div className="mb-4 space-y-3">
              <div>
                <label
                  htmlFor="promote-tag"
                  className="mb-1 block text-xs text-[color:var(--color-text-secondary)]"
                >
                  태그 (필수)
                </label>
                <input
                  id="promote-tag"
                  type="text"
                  value={tag}
                  onChange={(e) => setTag(e.target.value)}
                  placeholder="예: 2026-04-25-1430-JK"
                  className="w-full rounded border border-[color:var(--color-border-default)] bg-[color:var(--color-bg-elevated)] px-2 py-1.5 text-xs text-[color:var(--color-text-primary)]"
                />
                <p className="mt-1 text-tiny text-[color:var(--color-text-tertiary)]">
                  changed_by 는 자동으로 BASELINE_&lt;tag&gt;_&lt;UTC iso&gt;
                  형식으로 저장됩니다.
                </p>
              </div>

              <div>
                <label
                  htmlFor="promote-created-by"
                  className="mb-1 block text-xs text-[color:var(--color-text-secondary)]"
                >
                  승인자 이니셜 (필수)
                </label>
                <input
                  id="promote-created-by"
                  type="text"
                  value={createdBy}
                  onChange={(e) => setCreatedBy(e.target.value)}
                  placeholder="예: JK"
                  maxLength={10}
                  className="w-32 rounded border border-[color:var(--color-border-default)] bg-[color:var(--color-bg-elevated)] px-2 py-1.5 text-xs text-[color:var(--color-text-primary)]"
                />
              </div>

              <div>
                <label
                  htmlFor="promote-approval-note"
                  className="mb-1 block text-xs text-[color:var(--color-text-secondary)]"
                >
                  승인 근거 (필수)
                </label>
                <textarea
                  id="promote-approval-note"
                  value={approvalNote}
                  onChange={(e) => setApprovalNote(e.target.value)}
                  placeholder="이 베이스라인을 승인하는 사유를 적어주세요."
                  rows={3}
                  className="w-full rounded border border-[color:var(--color-border-default)] bg-[color:var(--color-bg-elevated)] px-2 py-1.5 text-xs text-[color:var(--color-text-primary)]"
                />
              </div>
            </div>

            <footer className="flex justify-between gap-2">
              <button
                type="button"
                onClick={() => setStep("diff")}
                disabled={submitting}
                className="px-3 py-1.5 text-xs font-medium rounded transition-colors text-[color:var(--color-text-primary)] hover:bg-[color:var(--color-bg-muted)] disabled:opacity-50"
              >
                이전
              </button>
              <div className="flex gap-2">
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
                  disabled={!canSubmit}
                  onClick={() => void handlePromote()}
                  className="px-3 py-1.5 text-xs font-medium rounded transition-opacity text-[color:var(--color-text-inverse)] bg-[color:var(--color-brand-primary)] hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {submitting ? "생성 중..." : "베이스라인 생성"}
                </button>
              </div>
            </footer>
          </section>
        )}
      </div>
    </div>
  );
}
