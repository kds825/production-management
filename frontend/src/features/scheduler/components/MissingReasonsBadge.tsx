"use client";

/**
 * Week 5 Task 5B.2 — gantt 헤더 "사유 미기록 N건" 배지.
 *
 * 동작:
 *   - mount 시 1회 fetch.
 *   - 토스트(reason-prompt) 가 닫힐 때마다 store.refresh() 가 외부에서 호출돼
 *     count 가 갱신된다 (push 모델 — polling 없음).
 *   - count === 0 이면 노출 안함 (헤더 영역 시각 부담 최소화).
 *
 * 컬러:
 *   var(--color-warning) — Tailwind arbitrary value 로 디자인 시스템 토큰 준수.
 *   디자인 시스템 룰: raw hex 금지, samildevkit 토큰만 사용 (verify-pwc-design 통과 목적).
 */
import { useEffect } from "react";
import { useMissingReasonsStore } from "../store/missingReasonsStore";

export function MissingReasonsBadge() {
  const count = useMissingReasonsStore((s) => s.count);
  const refresh = useMissingReasonsStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (count <= 0) return null;

  return (
    <span
      // role="status" 는 aria-live="polite" 의미 — 신규 미기록 발생 시 스크린리더가
      // 비강제 알림. 작업 흐름을 방해하지 않으면서 운영자에게 "처리할 게 있다"
      // 신호를 준다.
      role="status"
      aria-label={`사유 미기록 ${count}건 — 관리자 일괄 검토 필요`}
      title="드래그-드롭 후 사유가 입력되지 않은 변경 세트 수"
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-small font-medium border bg-[color:var(--color-bg-elevated)] border-[color:var(--color-warning)] text-[color:var(--color-warning)]"
    >
      <svg
        width="10"
        height="10"
        viewBox="0 0 16 16"
        fill="currentColor"
        aria-hidden="true"
      >
        <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM7.25 4h1.5v5h-1.5V4zm0 6h1.5v1.5h-1.5V10z" />
      </svg>
      사유 미기록 {count}건
    </span>
  );
}
