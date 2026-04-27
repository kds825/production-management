"use client";

/**
 * 간트 compareMode 툴바에 표시되는 카테고리 필터 pill (추가/이동/삭제).
 *
 * 시각 디자인 (스펙 §2 비교 모드 ON 상태 / §Accessibility):
 *   - 왼쪽 색 dot (6px) + 오른쪽 텍스트 라벨 — 색 의존을 피하고자 텍스트 필수.
 *   - active=true  → dot full opacity, 텍스트 검정
 *   - active=false → dot 30% opacity, 텍스트 회색 (토글 OFF 상태 명확히 표시)
 *   - hover       → bg-gray-50 (스케줄러 헤더의 자동배열/이전버전비교 버튼과 톤 통일)
 *   - focus       → KBI primary (var(--color-brand-primary)) outline — 기존 포커스 링 규약 재사용
 *
 * 크기는 기존 헤더 버튼(자동배열 등) 의 px-3/py-1.5/text-small 보다 한 단계 작은
 * px-2/py-1/text-small 로 잡아 pill 이 세 개 나열돼도 툴바가 복잡해지지 않도록 한다.
 *
 * ARIA (스펙 §Accessibility):
 *   - `aria-pressed` 로 토글 상태 노출 (role=button 은 실제 button 요소라 자동 부여)
 *   - `aria-label` 은 호출부에서 "추가 61건 보기 (토글)" 같은 맥락 문자열을 주입
 */

import React from "react";

interface FilterPillProps {
  /** 좌측 dot 배경색 (hex 권장, rgba 도 허용) */
  color: string;
  /** 표시 라벨 — 예: "추가 61" */
  label: string;
  /** 토글 상태 — true 면 해당 카테고리 overlay 표시 중 */
  active: boolean;
  /** 클릭 핸들러 — 상위 store 의 toggleCompareFilter 를 감싼 콜백 */
  onClick: () => void;
  /** 스크린리더용 라벨 — 기본값은 label + 토글 안내 */
  ariaLabel?: string;
}

const FilterPill: React.FC<FilterPillProps> = ({
  color,
  label,
  active,
  onClick,
  ariaLabel,
}) => {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      aria-label={ariaLabel ?? `${label} (토글)`}
      // 왜 inline style 의 outline: focus 시 KBI primary 링을 브라우저 기본 대신 강제.
      //   Tailwind focus:outline-* 클래스는 프로젝트마다 plugin 설정 편차가 커
      //   a11y 요구를 확실히 만족하지 못할 수 있어 JSX 레벨에서 보장한다.
      className={[
        "inline-flex items-center gap-1.5 px-2 py-1 text-small rounded border border-gray-200",
        "transition-colors hover:bg-gray-50",
        "focus:outline-none focus-visible:outline-[var(--color-brand-primary)]",
        active ? "text-gray-900" : "text-gray-400",
      ].join(" ")}
      style={{
        // focus-visible 일 때 2px solid primary, offset 1 — 기존 브랜드 규약과 일치.
        // focus-visible 지원이 없는 브라우저에서도 outline 이 뜨도록 style 로 백업.
        outlineOffset: 1,
      }}
      onFocus={(e) => {
        // 키보드 포커스 시에만 KBI primary 링 — 마우스 클릭 시 outline 방지
        if (e.currentTarget.matches(":focus-visible")) {
          e.currentTarget.style.outline =
            "2px solid var(--color-brand-primary)";
        }
      }}
      onBlur={(e) => {
        e.currentTarget.style.outline = "";
      }}
    >
      <span
        // 색 dot — 배경색으로 의미 전달, active 상태에 따라 opacity 조절
        aria-hidden="true"
        className="inline-block rounded-full"
        style={{
          width: 6,
          height: 6,
          backgroundColor: color,
          opacity: active ? 1 : 0.3,
        }}
      />
      <span>{label}</span>
    </button>
  );
};

export default FilterPill;
