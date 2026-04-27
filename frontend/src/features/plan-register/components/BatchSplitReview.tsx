"use client";

import { useState, useEffect, useCallback } from "react";

const PRIMARY = "var(--color-brand-primary)";
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/* ── Types ────────────────────────────────────────────── */

interface DrumChunk {
  lot_index: number;
  order_count: number;
  total_m: number;
  min_due: string;
  max_due: string;
  order_ids: string[];
  batch_ids?: number[];
  has_urgent?: boolean;
  min_priority?: number;
  days_until_due?: number;
}

interface SplitCandidate {
  batch_group: string;
  equipment_code: string | null;
  sq_mm2: number;
  lot_count: number;
  total_length_m: number;
  proposed_splits: DrumChunk[];
  gaps_days: number[];
  equipment_load_hours: number;
  auto_split_recommended?: boolean;
  urgency_reason?: string;
}

interface Props {
  candidates: SplitCandidate[];
  runLabel: string;
  gapDays: number;
  onGapDaysChange: (days: number) => void;
  onApplied: () => void;
  onClose: () => void;
}

/* ── Main Component ──────────────────────────────────── */

export function BatchSplitReview({
  candidates,
  runLabel,
  gapDays,
  onGapDaysChange,
  onApplied,
  onClose,
}: Props) {
  // 부모 레벨 상태: batch_group → 가위 활성화 배열
  const [allCuts, setAllCuts] = useState<Record<string, boolean[]>>({});
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // gapDays 변경 또는 초기화 시 모든 cuts 재계산
  useEffect(() => {
    const next: Record<string, boolean[]> = {};
    for (const c of candidates) {
      next[c.batch_group] = c.gaps_days.map((g) => g >= gapDays);
    }
    setAllCuts(next);
  }, [gapDays, candidates]);

  const toggleCut = useCallback((batchGroup: string, i: number) => {
    setAllCuts((prev) => {
      const arr = [...(prev[batchGroup] || [])];
      arr[i] = !arr[i];
      return { ...prev, [batchGroup]: arr };
    });
  }, []);

  // 분할 요약 계산
  const splitSummary = candidates
    .map((c) => {
      const cuts = allCuts[c.batch_group] || [];
      const splitCount = cuts.filter(Boolean).length + 1;
      return { sq: c.sq_mm2, splitCount, candidate: c, cuts };
    })
    .filter((s) => s.splitCount > 1);

  const handleApply = useCallback(async () => {
    if (applying || splitSummary.length === 0) return;
    setApplying(true);
    setError(null);

    try {
      for (const { candidate: c, cuts } of splitSummary) {
        // 가위 기준으로 청크 그룹 생성
        const groups: number[][] = [];
        let cur: number[] = [0];
        for (let i = 0; i < cuts.length; i++) {
          if (cuts[i]) {
            groups.push(cur);
            cur = [i + 1];
          } else {
            cur.push(i + 1);
          }
        }
        groups.push(cur);

        // 첫 번째 그룹은 원래 batch_group에 유지, 나머지만 split API 호출
        for (let g = 1; g < groups.length; g++) {
          const batchIds: number[] = [];
          for (const ci of groups[g]) {
            const chunk = c.proposed_splits[ci];
            if (chunk?.batch_ids) {
              batchIds.push(...chunk.batch_ids);
            }
          }
          if (batchIds.length === 0) continue;

          const res = await fetch(
            `${API}/pipeline/batch-group/${encodeURIComponent(c.batch_group)}/split`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                batch_ids: batchIds,
                suffix: `${g + 1}차`,
              }),
            },
          );
          if (!res.ok) {
            throw new Error(`${c.batch_group} 분할 실패: ${await res.text()}`);
          }
        }
      }
      onApplied();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "분할 중 오류 발생");
    } finally {
      setApplying(false);
    }
  }, [applying, splitSummary, onApplied, onClose]);

  if (!candidates || candidates.length === 0) return null;

  return (
    <div className="space-y-4">
      {/* Gap threshold control */}
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-blue-400">
          납기 차이가 큰 배치 그룹이 감지되었습니다
          {candidates.some((c) => c.auto_split_recommended) && (
            <span
              className="ml-2 rounded px-1 py-0.5 font-semibold"
              style={{ backgroundColor: "var(--status-warning-bg)", color: "var(--status-warning-text)" }}
            >
              ⚡ 긴급 포함
            </span>
          )}
        </span>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-gray-500">납기 차이 기준</span>
          <input
            type="number"
            min={1}
            max={30}
            value={gapDays}
            onChange={(e) => onGapDaysChange(Number(e.target.value) || 3)}
            className="w-10 text-center text-xs border rounded px-1 py-0.5"
            style={{ borderColor: "#93C5FD" }}
          />
          <span className="text-[10px] text-gray-500">일</span>
        </div>
      </div>

      {/* Candidate cards */}
      {candidates.map((c, idx) => {
        const cuts = allCuts[c.batch_group] || [];
        const splitCount = cuts.filter(Boolean).length + 1;
        return (
          <CandidateCard
            key={c.batch_group}
            candidate={c}
            index={idx}
            cuts={cuts}
            splitCount={splitCount}
            onToggleCut={(i) => toggleCut(c.batch_group, i)}
          />
        );
      })}

      {/* Error */}
      {error && (
        <div
          className="rounded-md p-2 text-xs"
          style={{
            backgroundColor: "var(--kbi-red-tint-5)",
            color: "#991B1B",
            border: "1px solid #FECACA",
          }}
        >
          {error}
        </div>
      )}

      {/* Footer */}
      <div
        className="flex items-center justify-between pt-3"
        style={{ borderTop: "1px solid var(--color-border-default)" }}
      >
        <div className="text-[11px] text-gray-500">
          {splitSummary.length > 0 ? (
            <span>
              분할 대상:{" "}
              {splitSummary
                .map((s) => `${s.sq}SQ → ${s.splitCount}개`)
                .join(", ")}
            </span>
          ) : (
            <span>분할 대상 없음</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onClose}
            className="text-[11px] font-medium px-4 py-2 rounded-md transition-colors"
            style={{
              border: "1px solid var(--neutral-300)",
              color: "var(--color-text-secondary)",
            }}
          >
            취소
          </button>
          <button
            onClick={handleApply}
            disabled={applying || splitSummary.length === 0}
            className="text-[11px] font-medium px-4 py-2 rounded-md text-white transition-opacity disabled:opacity-40"
            style={{ backgroundColor: PRIMARY }}
          >
            {applying
              ? "적용 중..."
              : splitSummary.length > 0
                ? `${splitSummary.length}건 분할 적용`
                : "변경 없음"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Candidate Card (controlled, no local state) ─────── */

function CandidateCard({
  candidate: c,
  index,
  cuts,
  splitCount,
  onToggleCut,
}: {
  candidate: SplitCandidate;
  index: number;
  cuts: boolean[];
  splitCount: number;
  onToggleCut: (i: number) => void;
}) {
  return (
    <div
      className="rounded-md p-3 space-y-3"
      style={{
        border: c.auto_split_recommended
          ? "1px solid var(--status-warning-border)"
          : "1px solid var(--status-info-bg)",
        backgroundColor: c.auto_split_recommended
          ? "var(--status-warning-bg)"
          : "var(--bg-surface)",
      }}
    >
      {/* Title row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className="text-[10px] font-bold rounded px-1.5 py-0.5"
            style={{ backgroundColor: "var(--kbi-red-tint-5)", color: PRIMARY }}
          >
            {index + 1}
          </span>
          <span className="text-xs font-semibold text-gray-800">
            연선 {c.sq_mm2}SQ
          </span>
          <span className="text-[10px] text-gray-400">
            {c.lot_count}틀 · {c.total_length_m.toLocaleString()}m
          </span>
          {c.auto_split_recommended && (
            <span
              className="text-[9px] font-semibold rounded px-1 py-0.5"
              style={{ backgroundColor: "var(--status-warning-bg)", color: "var(--status-warning-text)" }}
              title={c.urgency_reason}
            >
              ⚡ 자동분할 권고
            </span>
          )}
        </div>
        <span
          className="text-[10px] font-medium"
          style={{
            color: splitCount > 1 ? "var(--status-info-text)" : "var(--color-text-tertiary)",
          }}
        >
          {splitCount > 1 ? `${splitCount}개로 분할` : "분할 없음"}
        </span>
      </div>

      {/* Timeline bar */}
      <div className="flex items-center gap-0 overflow-x-auto py-1">
        {c.proposed_splits.map((chunk, i) => (
          <div key={i} className="flex items-center">
            <div
              className="rounded px-2 py-1.5 text-center min-w-[80px]"
              style={{
                backgroundColor: chunk.has_urgent
                  ? "var(--status-warning-bg)"
                  : i % 2 === 0
                    ? "var(--neutral-100)"
                    : "var(--color-border-default)",
                border: chunk.has_urgent
                  ? "1px solid var(--status-warning-border)"
                  : "1px solid var(--neutral-300)",
              }}
            >
              <div className="text-[10px] font-medium text-gray-700">
                {chunk.order_count}수주
                {chunk.has_urgent && (
                  <span className="ml-0.5" style={{ color: "var(--status-warning)" }}>
                    ⚡
                  </span>
                )}
              </div>
              <div className="text-[10px] text-gray-500">
                {chunk.total_m.toLocaleString()}m
              </div>
              <div
                className="text-[9px]"
                style={{
                  color:
                    chunk.days_until_due !== undefined &&
                    chunk.days_until_due <= 7
                      ? "var(--color-danger)"
                      : "var(--color-text-tertiary)",
                }}
              >
                ~{chunk.max_due?.slice(5)}
                {chunk.days_until_due !== undefined &&
                  chunk.days_until_due <= 14 && (
                    <span className="ml-0.5">({chunk.days_until_due}일)</span>
                  )}
              </div>
            </div>

            {i < c.proposed_splits.length - 1 && (
              <button
                onClick={() => onToggleCut(i)}
                className="flex flex-col items-center mx-1 cursor-pointer"
                title={cuts[i] ? "클릭하여 분할 취소" : "클릭하여 여기서 분할"}
              >
                <span
                  className="text-[9px] font-medium"
                  style={{
                    color: cuts[i] ? PRIMARY : "var(--color-text-tertiary)",
                  }}
                >
                  {c.gaps_days[i]}일
                </span>
                <div
                  className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] transition-colors"
                  style={{
                    backgroundColor: cuts[i]
                      ? "var(--kbi-red-tint-5)"
                      : "var(--bg-surface-alt)",
                    border: `1.5px solid ${cuts[i] ? PRIMARY : "var(--neutral-300)"}`,
                    color: cuts[i] ? PRIMARY : "var(--color-text-tertiary)",
                  }}
                >
                  {cuts[i] ? "✂️" : "·"}
                </div>
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
