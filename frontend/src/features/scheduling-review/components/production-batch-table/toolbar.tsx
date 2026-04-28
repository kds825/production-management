/**
 * ProductionBatchTable 의 상단 바 (Task 1.17 보완, F-3).
 *
 * 추출 대상:
 * - 상태 범례 (진행 / 대기)
 * - 활성 필터 배지 + "전체 해제" 버튼
 * - 컬럼 설정 dropdown
 */

"use client";

import React from "react";
import { PROCESS_STATUS_COLORS } from "@/shared/constants/processGroups";
import { COL_DEFS, type ColKey } from "./columnDefs";

interface Props {
  hasActiveFilter: boolean;
  activeFilterCount: number;
  filteredBatchesCount: number;
  sortedBatchesCount: number;
  onClearAllFilters: () => void;
  colMenuOpen: boolean;
  setColMenuOpen: (updater: (prev: boolean) => boolean) => void;
  colMenuRef: React.RefObject<HTMLDivElement | null>;
  hiddenCols: Set<ColKey>;
  toggleCol: (key: ColKey) => void;
  clearHiddenCols: () => void;
}

export function ProductionBatchTableToolbar({
  hasActiveFilter,
  activeFilterCount,
  filteredBatchesCount,
  sortedBatchesCount,
  onClearAllFilters,
  colMenuOpen,
  setColMenuOpen,
  colMenuRef,
  hiddenCols,
  toggleCol,
  clearHiddenCols,
}: Props) {
  return (
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-4">
        {/* 상태 범례 */}
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full"
            style={{
              width: 8,
              height: 8,
              backgroundColor: PROCESS_STATUS_COLORS["진행"],
            }}
          />
          <span
            className="text-tiny"
            style={{ color: "var(--color-text-secondary)" }}
          >
            진행
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full"
            style={{
              width: 8,
              height: 8,
              backgroundColor: PROCESS_STATUS_COLORS["대기"],
            }}
          />
          <span
            className="text-tiny"
            style={{ color: "var(--color-text-secondary)" }}
          >
            대기
          </span>
        </div>
        {/* 활성 필터 배지 */}
        {hasActiveFilter && (
          <div className="flex items-center gap-1.5">
            <span
              className="text-tiny px-2 py-0.5 rounded-full font-medium"
              style={{
                backgroundColor: "var(--kbi-red-tint-5)",
                color: "var(--color-brand-primary)",
                border: "1px solid var(--kbi-red-tint-20)",
              }}
            >
              필터 {activeFilterCount}개 적용 중 ({filteredBatchesCount}/
              {sortedBatchesCount}행)
            </span>
            <button
              onClick={onClearAllFilters}
              className="text-tiny px-2 py-0.5 rounded"
              style={{
                color: "var(--color-text-secondary)",
                border: "1px solid var(--color-border-default)",
              }}
            >
              전체 해제
            </button>
          </div>
        )}
      </div>

      {/* 컬럼 설정 */}
      <div className="relative" ref={colMenuRef}>
        <button
          onClick={() => setColMenuOpen((v) => !v)}
          className="flex items-center gap-1 text-tiny px-2 py-1 rounded border transition-colors"
          style={{
            borderColor: colMenuOpen
              ? "var(--color-brand-primary)"
              : "var(--color-border-default)",
            color: colMenuOpen
              ? "var(--color-brand-primary)"
              : "var(--color-text-secondary)",
            backgroundColor: colMenuOpen
              ? "var(--kbi-red-tint-5)"
              : "var(--bg-surface)",
          }}
        >
          <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor">
            <path d="M9.796 1.343c-.527-1.79-3.065-1.79-3.592 0l-.094.319a.873.873 0 0 1-1.255.52l-.292-.16c-1.64-.892-3.433.902-2.54 2.541l.159.292a.873.873 0 0 1-.52 1.255l-.319.094c-1.79.527-1.79 3.065 0 3.592l.319.094a.873.873 0 0 1 .52 1.255l-.16.292c-.892 1.64.901 3.434 2.541 2.54l.292-.159a.873.873 0 0 1 1.255.52l.094.319c.527 1.79 3.065 1.79 3.592 0l.094-.319a.873.873 0 0 1 1.255-.52l.292.16c1.64.893 3.434-.902 2.54-2.541l-.159-.292a.873.873 0 0 1 .52-1.255l.319-.094c1.79-.527 1.79-3.065 0-3.592l-.319-.094a.873.873 0 0 1-.52-1.255l.16-.292c.893-1.64-.902-3.433-2.541-2.54l-.292.159a.873.873 0 0 1-1.255-.52l-.094-.319z" />
          </svg>
          컬럼 설정
          {hiddenCols.size > 0 && (
            <span
              className="ml-0.5 text-mini font-bold"
              style={{ color: "var(--color-brand-primary)" }}
            >
              -{hiddenCols.size}
            </span>
          )}
        </button>
        {colMenuOpen && (
          <div
            className="absolute right-0 mt-1 rounded-lg shadow-lg z-50 py-1"
            style={{
              backgroundColor: "var(--bg-surface)",
              border: "1px solid var(--color-border-default)",
              minWidth: 160,
              top: "100%",
            }}
          >
            {COL_DEFS.map((col) => (
              <label
                key={col.key}
                className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-gray-50"
              >
                <input
                  type="checkbox"
                  checked={!hiddenCols.has(col.key)}
                  onChange={() => toggleCol(col.key)}
                  className="w-3 h-3 accent-red-700"
                />
                <span
                  className="text-small"
                  style={{ color: "var(--neutral-text-primary)" }}
                >
                  {col.label}
                </span>
              </label>
            ))}
            {hiddenCols.size > 0 && (
              <>
                <div
                  style={{
                    borderTop: "1px solid var(--neutral-100)",
                    margin: "4px 0",
                  }}
                />
                <button
                  onClick={clearHiddenCols}
                  className="w-full text-left px-3 py-1 text-tiny"
                  style={{ color: "var(--color-text-secondary)" }}
                >
                  모두 표시
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
