"use client";

import { useState, useCallback } from "react";

const PRIMARY = "#C41230";
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/* ── Types ────────────────────────────────────────────── */

interface DrumChunk {
  lot_index: number;
  order_count: number;
  total_m: number;
  min_due: string; // YYYY-MM-DD
  max_due: string;
  order_ids: string[];
}

interface SplitCandidate {
  batch_group: string;
  equipment_code: string | null;
  sq: number;
  lot_count: number;
  total_length_m: number;
  chunks: DrumChunk[];
  gaps_days: number[]; // gap between chunk[i] and chunk[i+1]
  equip_load_hours: number;
}

interface Props {
  candidates: SplitCandidate[];
  runLabel: string;
  gapDays: number;
  onGapDaysChange: (days: number) => void;
  onSplitApplied: () => void; // Stage1 결과 새로고침 콜백
}

/* ── Component ────────────────────────────────────────── */

export function BatchSplitReview({
  candidates,
  runLabel,
  gapDays,
  onGapDaysChange,
  onSplitApplied,
}: Props) {
  if (!candidates || candidates.length === 0) return null;

  return (
    <div
      className="rounded-lg p-4 space-y-4"
      style={{
        border: "1px solid #DBEAFE",
        backgroundColor: "#F0F7FF",
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm">✂️</span>
          <span className="text-xs font-semibold" style={{ color: "#1E40AF" }}>
            배치 분할 검토
          </span>
          <span className="text-[10px] text-blue-400">
            납기 차이가 큰 배치 그룹이 감지되었습니다
          </span>
        </div>
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

      {/* Candidates */}
      {candidates.map((c, idx) => (
        <CandidateCard
          key={c.batch_group}
          candidate={c}
          index={idx}
          runLabel={runLabel}
          onSplitApplied={onSplitApplied}
        />
      ))}
    </div>
  );
}

/* ── Candidate Card ───────────────────────────────────── */

function CandidateCard({
  candidate: c,
  index,
  runLabel,
  onSplitApplied,
}: {
  candidate: SplitCandidate;
  index: number;
  runLabel: string;
  onSplitApplied: () => void;
}) {
  // 각 gap 위치에 분할선 활성화/비활성화 상태
  const [cuts, setCuts] = useState<boolean[]>(
    () => c.gaps_days.map((g) => g >= 3), // 기본: gap >= threshold면 활성화
  );
  const [applying, setApplying] = useState(false);
  const [done, setDone] = useState(false);

  const splitCount = cuts.filter(Boolean).length + 1;

  const toggleCut = useCallback(
    (i: number) => {
      if (done) return;
      setCuts((prev) => {
        const next = [...prev];
        next[i] = !next[i];
        return next;
      });
    },
    [done],
  );

  const handleSplit = useCallback(async () => {
    if (applying || done) return;

    // 활성화된 분할선 기준으로 청크들을 그룹으로 묶기
    const groups: number[][] = []; // 각 그룹의 chunk indices
    let currentGroup: number[] = [0];

    for (let i = 0; i < cuts.length; i++) {
      if (cuts[i]) {
        groups.push(currentGroup);
        currentGroup = [i + 1];
      } else {
        currentGroup.push(i + 1);
      }
    }
    groups.push(currentGroup);

    if (groups.length <= 1) return; // 분할선 없으면 skip

    setApplying(true);
    try {
      // 첫 번째 그룹은 원래 batch_group에 남기고, 나머지 그룹을 split
      for (let g = 1; g < groups.length; g++) {
        const chunkIndices = groups[g];
        const batchIds: number[] = [];
        for (const ci of chunkIndices) {
          const chunk = c.chunks[ci];
          if (
            chunk &&
            (chunk as unknown as { batch_ids?: number[] }).batch_ids
          ) {
            batchIds.push(
              ...(chunk as unknown as { batch_ids: number[] }).batch_ids,
            );
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
          const err = await res.text();
          console.error("Split failed:", err);
        }
      }
      setDone(true);
      onSplitApplied();
    } catch (err) {
      console.error("Split error:", err);
    } finally {
      setApplying(false);
    }
  }, [cuts, c, applying, done, onSplitApplied]);

  const handleKeep = useCallback(() => {
    setDone(true);
  }, []);

  if (done) {
    return (
      <div
        className="rounded-md p-3 text-xs"
        style={{
          border: "1px solid #D1FAE5",
          backgroundColor: "#ECFDF5",
          color: "#065F46",
        }}
      >
        {c.sq}SQ 연선 —{" "}
        {splitCount > 1 ? `${splitCount}개로 분할 완료` : "1개 배치 유지"}
      </div>
    );
  }

  return (
    <div
      className="rounded-md p-3 space-y-3"
      style={{
        border: "1px solid #BFDBFE",
        backgroundColor: "#FFFFFF",
      }}
    >
      {/* Title row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className="text-[10px] font-bold rounded px-1.5 py-0.5"
            style={{ backgroundColor: "#FEF2F2", color: PRIMARY }}
          >
            {index + 1}
          </span>
          <span className="text-xs font-semibold text-gray-800">
            연선 {c.sq}SQ
          </span>
          <span className="text-[10px] text-gray-400">
            {c.lot_count}틀 · {c.total_length_m.toLocaleString()}m
          </span>
          {c.equipment_code && (
            <span className="text-[10px] text-gray-400">
              · {c.equipment_code}
              {c.equip_load_hours > 0 && (
                <span className="text-orange-500 ml-1">
                  ({Math.round(c.equip_load_hours)}h 부하)
                </span>
              )}
            </span>
          )}
        </div>
        <span className="text-[10px] text-blue-600 font-medium">
          {splitCount > 1 ? `${splitCount}개로 분할` : "분할 없음"}
        </span>
      </div>

      {/* Timeline bar: chunks with cut toggles */}
      <div className="flex items-center gap-0 overflow-x-auto py-1">
        {c.chunks.map((chunk, i) => (
          <div key={i} className="flex items-center">
            {/* Chunk block */}
            <div
              className="rounded px-2 py-1.5 text-center min-w-[80px]"
              style={{
                backgroundColor: i % 2 === 0 ? "#F3F4F6" : "#E5E7EB",
                border: "1px solid #D1D5DB",
              }}
            >
              <div className="text-[10px] font-medium text-gray-700">
                {chunk.order_count}수주
              </div>
              <div className="text-[10px] text-gray-500">
                {chunk.total_m.toLocaleString()}m
              </div>
              <div className="text-[9px] text-gray-400">
                ~{chunk.max_due.slice(5)}
              </div>
            </div>

            {/* Gap / cut toggle between chunks */}
            {i < c.chunks.length - 1 && (
              <button
                onClick={() => toggleCut(i)}
                className="flex flex-col items-center mx-1 cursor-pointer group"
                title={cuts[i] ? "클릭하여 분할 취소" : "클릭하여 여기서 분할"}
              >
                <span
                  className="text-[9px] font-medium"
                  style={{
                    color: cuts[i] ? PRIMARY : "#9CA3AF",
                  }}
                >
                  {c.gaps_days[i]}일
                </span>
                <div
                  className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] transition-colors"
                  style={{
                    backgroundColor: cuts[i] ? "#FEF2F2" : "#F9FAFB",
                    border: `1.5px solid ${cuts[i] ? PRIMARY : "#D1D5DB"}`,
                    color: cuts[i] ? PRIMARY : "#9CA3AF",
                  }}
                >
                  {cuts[i] ? "✂️" : "·"}
                </div>
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={handleSplit}
          disabled={applying || splitCount <= 1}
          className="text-[11px] font-medium px-3 py-1.5 rounded-md text-white transition-opacity disabled:opacity-40"
          style={{ backgroundColor: PRIMARY }}
        >
          {applying ? "분할 중..." : `${splitCount}개로 분할`}
        </button>
        <button
          onClick={handleKeep}
          className="text-[11px] font-medium px-3 py-1.5 rounded-md transition-colors"
          style={{
            border: "1px solid #D1D5DB",
            color: "#6B7280",
          }}
        >
          하나로 유지
        </button>
      </div>
    </div>
  );
}
