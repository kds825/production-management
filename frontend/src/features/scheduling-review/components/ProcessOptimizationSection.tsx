"use client";

import { useState, useCallback, useRef } from "react";
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
  const [highlightedBatchId, setHighlightedBatchId] = useState<string | null>(
    null,
  );
  const [activeWipId, setActiveWipId] = useState<string | null>(null);
  const batchTableRef = useRef<HTMLDivElement>(null);
  const wipTableRef = useRef<HTMLDivElement>(null);
  const lastClickedWipRef = useRef<string | null>(null);

  const handleWipClick = useCallback(
    (matchedBatchId: string) => {
      setHighlightedBatchId(matchedBatchId);

      const wip = wipItems?.find((w) => w.matchedBatchId === matchedBatchId);
      setActiveWipId(wip?.id ?? null);
      lastClickedWipRef.current = wip?.id ?? null;

      // 배치 테이블에서 해당 행으로 스크롤
      setTimeout(() => {
        const row = batchTableRef.current?.querySelector(
          `[data-batch-id="${matchedBatchId}"]`,
        );
        if (row) {
          row.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }, 50);
    },
    [wipItems],
  );

  const handleBackToWip = useCallback(() => {
    // WIP 테이블로 스크롤 복귀
    if (wipTableRef.current) {
      wipTableRef.current.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
    setHighlightedBatchId(null);
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
      <h3 className="text-sm font-semibold mb-3" style={{ color: "#111827" }}>
        {sectionNumber}. {title}
      </h3>

      <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
        <div
          ref={batchTableRef}
          style={{ flex: "1 1 0%", minWidth: hasWip ? 800 : 0 }}
        >
          <ProductionBatchTable
            title={title}
            batches={batches}
            processGroup={processGroup}
            highlightedBatchId={highlightedBatchId}
            onBatchWipClick={hasWip ? handleBatchWipClick : undefined}
          />
        </div>

        {hasWip && (
          <div ref={wipTableRef} style={{ flex: "0 0 288px", minWidth: 0 }}>
            <WipInventoryTable
              title={wipTitle}
              items={wipItems}
              onWipClick={handleWipClick}
              activeWipId={activeWipId}
            />
          </div>
        )}
      </div>

      {/* 배치 하이라이트 중 → WIP로 돌아가기 버튼 */}
      {highlightedBatchId && (
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
            ← WIP 재고로 돌아가기
          </button>
        </div>
      )}
    </section>
  );
}
