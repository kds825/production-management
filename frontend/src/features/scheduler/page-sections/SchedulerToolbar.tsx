/**
 * SchedulerToolbar — 스케줄러 페이지 상단 툴바.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - ViewFilter / MissingReasonsBadge / SyncButton / ZoomControl 합성
 *   - 자동배열 버튼 + 진행 상태 표시
 *   - 비교 모드 토글 + 필터 pills + "목록" 버튼 + 에러/변화없음 안내 + a11y live region
 *   - 납기 초과 토글 + 카운트 배지
 *   - SM재고 실적 모달 트리거 (편집 모드일 때만)
 *
 * 부모(SchedulerPage) 가 모든 상태/콜백을 props 로 주입한다 — 상태 lifting 으로
 * 일관성을 유지하고 useBatchCompareMode/useSchedulerData 훅 결과를 단일 통로로 연결.
 *
 * NOTE: 이 컴포넌트는 page-section/scheduler-view 의 Toolbar 와 별개의 모듈이다.
 * 전자는 페이지 헤더 도구 모음, 후자는 간트 하단 표시 옵션 토글.
 */
"use client";

import { ViewFilter } from "../components/ViewFilter";
import { MissingReasonsBadge } from "../components/MissingReasonsBadge";
import { SyncButton } from "../components/SyncButton";
import { ZoomControl } from "../components/ZoomControl";
import FilterPill from "../components/FilterPill";
import { KBI_BRAND } from "@/shared/constants/brand";
import type { RunCompareResponse } from "../types/diff";

interface SchedulerToolbarProps {
  // ── 자동배열 ──
  autoScheduleLoading: boolean;
  onAutoSchedule: () => void;

  // ── 비교 모드 ──
  compareModeEnabled: boolean;
  compareModeLoading: boolean;
  compareModeError: string | null;
  diffResponse: RunCompareResponse | null;
  diffFilters: { added: boolean; moved: boolean; removed: boolean };
  onToggleCompareMode: () => void;
  onToggleCompareFilter: (cat: "added" | "moved" | "removed") => void;
  onOpenCompareList: () => void;

  // ── 납기 초과 ──
  lateTaskCount: number;
  showLatePanel: boolean;
  onToggleLatePanel: () => void;

  // ── SM재고 실적 (편집 모드일 때만 노출) ──
  isEditMode: boolean;
  wipRunLabel: string | null;
  onOpenWipModal: () => void;
}

export function SchedulerToolbar({
  autoScheduleLoading,
  onAutoSchedule,
  compareModeEnabled,
  compareModeLoading,
  compareModeError,
  diffResponse,
  diffFilters,
  onToggleCompareMode,
  onToggleCompareFilter,
  onOpenCompareList,
  lateTaskCount,
  showLatePanel,
  onToggleLatePanel,
  isEditMode,
  wipRunLabel,
  onOpenWipModal,
}: SchedulerToolbarProps) {
  return (
    <div className="flex items-center bg-white border-b border-gray-200">
      <div className="flex-1">
        <ViewFilter />
      </div>
      {/* Week 5 Task 5B.2 — 사유 미기록 배지. count=0 이면 자체 hide. */}
      <div className="px-3 py-2 shrink-0">
        <MissingReasonsBadge />
      </div>
      <div className="px-4 py-2 shrink-0 border-l border-gray-200">
        <SyncButton />
      </div>
      <div className="px-4 py-2 shrink-0 border-l border-gray-200">
        <ZoomControl />
      </div>

      {/* 자동배열 버튼 */}
      <div className="px-3 py-2 shrink-0 border-l border-gray-200">
        <button
          onClick={onAutoSchedule}
          disabled={autoScheduleLoading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium text-white transition-opacity disabled:opacity-50"
          style={{ backgroundColor: "var(--color-brand-primary)" }}
          title="최신 런에 대해 Stage 2 자동배열 실행"
        >
          {autoScheduleLoading ? (
            <span
              className="inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full"
              style={{ animation: "spin 1s linear infinite" }}
            />
          ) : (
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 2a6 6 0 100 12A6 6 0 008 2zm0 1.5a4.5 4.5 0 110 9 4.5 4.5 0 010-9zm-.75 2v3.19l2.47 1.43.75-1.3L8.75 7.5V5.5h-1.5z" />
            </svg>
          )}
          자동배열
        </button>
      </div>

      {/* Wave 3 — 비교 모드 토글 + 필터 pills */}
      <div className="px-3 py-2 shrink-0 border-l border-gray-200 flex items-center gap-2">
        <button
          onClick={onToggleCompareMode}
          disabled={compareModeLoading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium transition-colors disabled:opacity-60"
          style={{
            backgroundColor: compareModeEnabled ? "#1E40AF" : "#EFF6FF",
            color: compareModeEnabled ? "var(--color-text-inverse)" : "#1E40AF",
            border: "1px solid #BFDBFE",
          }}
          title={
            compareModeEnabled
              ? "비교 모드 끄기 (ESC)"
              : "최신 런과 이전 버전 비교"
          }
          aria-pressed={compareModeEnabled}
        >
          {compareModeLoading ? (
            <span
              className="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full"
              style={{ animation: "spin 1s linear infinite" }}
            />
          ) : (
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 13V2a6 6 0 010 12z" />
            </svg>
          )}
          {compareModeEnabled ? "비교 모드 ×" : "비교 모드"}
        </button>

        {compareModeEnabled && diffResponse && (
          <>
            <FilterPill
              color={KBI_BRAND.colors.diff.added}
              label={`추가 ${diffResponse.summary.added}`}
              active={diffFilters.added}
              onClick={() => onToggleCompareFilter("added")}
              ariaLabel={`추가 ${diffResponse.summary.added}건 보기 (토글)`}
            />
            <FilterPill
              color={KBI_BRAND.colors.diff.moved}
              label={`이동 ${diffResponse.summary.moved}`}
              active={diffFilters.moved}
              onClick={() => onToggleCompareFilter("moved")}
              ariaLabel={`이동 ${diffResponse.summary.moved}건 보기 (토글)`}
            />
            <FilterPill
              color={KBI_BRAND.colors.diff.removed}
              label={`삭제 ${diffResponse.summary.removed}`}
              active={diffFilters.removed}
              onClick={() => onToggleCompareFilter("removed")}
              ariaLabel={`삭제 ${diffResponse.summary.removed}건 보기 (토글)`}
            />
            <button
              onClick={onOpenCompareList}
              className="px-2 py-1 text-[11px] rounded border border-gray-200 hover:bg-gray-50"
              aria-haspopup="dialog"
            >
              목록
            </button>
          </>
        )}

        {compareModeError && (
          <span className="text-[10px] text-red-600 ml-2">
            {compareModeError}
          </span>
        )}

        {compareModeEnabled &&
          diffResponse &&
          diffResponse.summary.added === 0 &&
          diffResponse.summary.moved === 0 &&
          diffResponse.summary.removed === 0 && (
            <span className="text-[10px] text-green-700 ml-2">
              ✓ 두 버전이 동일합니다
            </span>
          )}

        <div aria-live="polite" className="sr-only">
          {compareModeEnabled && diffResponse
            ? `비교 모드 활성. 추가 ${diffResponse.summary.added}, 이동 ${diffResponse.summary.moved}, 삭제 ${diffResponse.summary.removed}`
            : ""}
        </div>
      </div>

      {/* 납기 초과 현황 버튼 */}
      <div className="px-3 py-2 shrink-0 border-l border-gray-200">
        <button
          onClick={onToggleLatePanel}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium transition-colors"
          style={{
            backgroundColor:
              lateTaskCount > 0
                ? showLatePanel
                  ? "#7F1D1D"
                  : "var(--kbi-red-tint-12)"
                : "var(--neutral-100)",
            color:
              lateTaskCount > 0
                ? showLatePanel
                  ? "#FCA5A5"
                  : "#B91C1C"
                : "var(--color-text-secondary)",
            border: `1px solid ${lateTaskCount > 0 ? "#FECACA" : "var(--color-border-default)"}`,
          }}
          title="납기를 초과한 배치 목록 보기"
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
            <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm.75 3.5v4.25l3 1.73-.75 1.3L7.25 9.5V4.5h1.5z" />
          </svg>
          납기 초과
          {lateTaskCount > 0 && (
            <span
              className="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-bold text-white"
              style={{ backgroundColor: "var(--color-danger)" }}
            >
              {lateTaskCount}
            </span>
          )}
        </button>
      </div>

      {/* SM재고 실적 버튼 — 편집모드일 때만 표시 */}
      {isEditMode && (
        <div className="px-3 py-2 shrink-0 border-l border-gray-200">
          <button
            onClick={onOpenWipModal}
            disabled={!wipRunLabel}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium text-white transition-opacity disabled:opacity-50"
            style={{ backgroundColor: "var(--color-brand-primary)" }}
            title="SM재고 실적 업데이트"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
              <path d="M2 3h12v2H2V3zm0 4h12v2H2V7zm0 4h8v2H2v-2z" />
            </svg>
            SM재고 실적
          </button>
        </div>
      )}
    </div>
  );
}
