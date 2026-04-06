"use client";

import { useState, useCallback, useRef, useMemo } from "react";
import type { SchedulingBatch, WipItem } from "../types";
import type { ProcessGroup } from "@/shared/constants/processGroups";
import { ProductionBatchTable } from "./ProductionBatchTable";
import { WipInventoryTable } from "./WipInventoryTable";

interface ProcessOptimizationSectionProps {
  sectionNumber: number;
  title: string;
  processGroup: ProcessGroup;
  batches: SchedulingBatch[];
  wipItems?: WipItem[];
  wipTitle?: string;
}

export function ProcessOptimizationSection({
  sectionNumber,
  title,
  processGroup,
  batches,
  wipItems,
  wipTitle,
}: ProcessOptimizationSectionProps) {
  const hasWip = wipItems !== undefined && wipTitle;
  const [wipExpanded, setWipExpanded] = useState(true);
  const [highlightedBatchIds, setHighlightedBatchIds] = useState<Set<string>>(new Set());
  const [activeWipId, setActiveWipId] = useState<string | null>(null);
  const batchTableRef = useRef<HTMLDivElement>(null);
  const wipTableRef = useRef<HTMLDivElement>(null);

  // wip_id → 해당 WIP을 사용하는 모든 배치 ID 목록
  const wipBatchMap = useMemo(() => {
    const map = new Map<number, string[]>();
    for (const b of batches) {
      if (b.wip_matched_id != null) {
        const ids = map.get(b.wip_matched_id) ?? [];
        ids.push(b.id);
        map.set(b.wip_matched_id, ids);
      }
    }
    return map;
  }, [batches]);

  // WIP 아이템에 matchedBatchIds(1:N) 보강
  const enhancedWipItems = useMemo(
    () =>
      wipItems?.map((w) => ({
        ...w,
        matchedBatchIds:
          wipBatchMap.get(w.wip_id) ??
          (w.matchedBatchId ? [w.matchedBatchId] : []),
      })),
    [wipItems, wipBatchMap],
  );

  const handleWipClick = useCallback(
    (matchedBatchIds: string[]) => {
      setHighlightedBatchIds(new Set(matchedBatchIds));

      // 첫 번째 배치 ID로 연결된 WIP 행 활성화
      const wip = wipItems?.find((w) =>
        matchedBatchIds.some((id) => id === w.matchedBatchId),
      );
      setActiveWipId(wip?.id ?? null);

      // 배치 테이블에서 첫 번째 행으로 스크롤
      setTimeout(() => {
        const firstId = matchedBatchIds[0];
        if (!firstId) return;
        const row = batchTableRef.current?.querySelector(
          `[data-batch-id="${firstId}"]`,
        );
        if (row) {
          row.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }, 50);
    },
    [wipItems],
  );

  const handleBackToWip = useCallback(() => {
    if (wipTableRef.current) {
      wipTableRef.current.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
    setHighlightedBatchIds(new Set());
    setActiveWipId(null);
  }, []);

  // 배치→WIP: "재고 사용" 클릭 시 WIP 테이블 해당 행 하이라이트
  const handleBatchWipClick = useCallback(
    (batchId: string) => {
      const wip = wipItems?.find((w) => w.matchedBatchId === batchId);
      if (!wip) return;
      setActiveWipId(wip.id);

      // WIP 테이블 내 해당 행으로 스크롤 (data-wip-id 속성 사용)
      setTimeout(() => {
        const row = wipTableRef.current?.querySelector(
          `[data-wip-id="${wip.id}"]`,
        );
        if (row) {
          row.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      }, 50);

      setTimeout(() => setActiveWipId(null), 3000);
    },
    [wipItems],
  );

  return (
    <section className="mb-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold" style={{ color: "#111827" }}>
          {sectionNumber}. {title}
        </h3>
        {hasWip && (
          <button
            onClick={() => setWipExpanded((v) => !v)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors"
            style={{
              backgroundColor: wipExpanded ? "#EFF6FF" : "#F3F4F6",
              color: wipExpanded ? "#2563EB" : "#6B7280",
              border: `1px solid ${wipExpanded ? "#BFDBFE" : "#E5E7EB"}`,
            }}
          >
            <span
              style={{
                display: "inline-block",
                width: 6,
                height: 6,
                borderRadius: "50%",
                backgroundColor: wipExpanded ? "#2563EB" : "#9CA3AF",
              }}
            />
            SM 재고 {wipItems!.length}건
            <span style={{ fontSize: 9 }}>{wipExpanded ? "◀" : "▶"}</span>
          </button>
        )}
      </div>

      <div className="flex gap-4" style={{ alignItems: "flex-start" }}>
        <div ref={batchTableRef} style={{ flex: "1 1 0%", minWidth: 0 }}>
          <ProductionBatchTable
            title={title}
            batches={batches}
            processGroup={processGroup}
            highlightedBatchIds={highlightedBatchIds}
            onBatchWipClick={hasWip && wipExpanded ? handleBatchWipClick : undefined}
          />
        </div>

        {hasWip && wipExpanded && (
          <div ref={wipTableRef} style={{ flex: "0 0 268px", minWidth: 0 }}>
            <WipInventoryTable
              title={wipTitle}
              items={enhancedWipItems ?? wipItems ?? []}
              onWipClick={handleWipClick}
              activeWipId={activeWipId}
            />
          </div>
        )}
      </div>

      {/* 배치 하이라이트 중 → WIP로 돌아가기 버튼 */}
      {highlightedBatchIds.size > 0 && (
        <div className="mt-2 flex justify-end">
          <button
            onClick={handleBackToWip}
            className="text-[11px] font-medium px-3 py-1.5 rounded-md transition-colors"
            style={{
              backgroundColor: "#FEF2F2",
              color: "#C41230",
              border: "1px solid #FECACA",
            }}
          >
            ← SM 재고로 돌아가기
          </button>
        </div>
      )}
    </section>
  );
}
