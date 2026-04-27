/**
 * Toolbar — 간트 하단의 토글 바.
 *
 * 책임 (Single Responsibility):
 *   - 주말 컬럼 접기/펴기 토글
 *   - 빈 설비 행(배치 0건) 숨김 토글 + 숨겨진 설비 칩 목록
 *
 * 분리 이유 (Week 7 Task 7B.2):
 *   - 기존 SchedulerView 본문에 인라인 JSX 로 80여 줄 차지하여 view 의 메인 렌더 흐름을 가렸음.
 *   - "표시 옵션" 만 다루는 작은 컨트롤은 별도 컴포넌트로 두는 편이 가독성/재사용에 유리.
 *   - 상태(hideWeekends/hideEmpty/showHiddenList) 는 부모(<index>) 가 보유하고 props 로 주입.
 *     view 자체의 dayWidth 계산이 hideWeekends 에 의존하기 때문 (상태 lifting).
 */
"use client";

import type { Equipment } from "../../types";

interface SchedulerToolbarProps {
  hideWeekends: boolean;
  onToggleWeekends: () => void;
  hideEmpty: boolean;
  onToggleHideEmpty: () => void;
  showHiddenList: boolean;
  onToggleHiddenList: () => void;
  hiddenEquipment: Equipment[];
}

export function SchedulerToolbar({
  hideWeekends,
  onToggleWeekends,
  hideEmpty,
  onToggleHideEmpty,
  showHiddenList,
  onToggleHiddenList,
  hiddenEquipment,
}: SchedulerToolbarProps) {
  return (
    <div
      className="flex items-center gap-2 px-3 py-1.5 mt-1 rounded-md flex-wrap"
      style={{
        backgroundColor: "var(--neutral-100)",
        border: "1px solid var(--color-border-default)",
      }}
    >
      {/* 주말 열 접기/펴기 */}
      <button
        onClick={onToggleWeekends}
        className="flex items-center gap-1.5 text-small font-medium transition-colors"
        style={{
          color: hideWeekends
            ? "var(--color-brand-primary)"
            : "var(--color-text-secondary)",
        }}
      >
        <span>{hideWeekends ? "▶" : "▼"}</span>
        <span>{hideWeekends ? "주말 접힘" : "주말 펼침"}</span>
      </button>

      <span className="text-gray-300 select-none">|</span>

      <button
        onClick={onToggleHideEmpty}
        className="flex items-center gap-1.5 text-small font-medium text-gray-600 hover:text-gray-900 transition-colors"
      >
        <span>{hideEmpty ? "▶" : "▼"}</span>
        <span>
          {hideEmpty
            ? `빈 설비 ${hiddenEquipment.length}개 숨김`
            : "빈 설비 표시 중"}
        </span>
      </button>

      {hideEmpty && hiddenEquipment.length > 0 && (
        <>
          <span className="text-gray-300 select-none">|</span>
          <button
            onClick={onToggleHiddenList}
            className="text-small text-blue-500 hover:text-blue-700 transition-colors"
          >
            {showHiddenList ? "목록 닫기" : "목록 보기"}
          </button>
        </>
      )}

      {hideEmpty && showHiddenList && hiddenEquipment.length > 0 && (
        <div className="flex flex-wrap gap-1 ml-1">
          {hiddenEquipment.map((eq) => (
            <span
              key={eq.id}
              className="text-tiny px-1.5 py-0.5 rounded"
              style={{
                backgroundColor: "var(--color-border-default)",
                color: "var(--color-text-secondary)",
              }}
              title={eq.process_type}
            >
              {eq.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
