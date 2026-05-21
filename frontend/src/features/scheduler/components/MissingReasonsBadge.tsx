"use client";

/**
 * Week 5 Task 5B.2 — gantt 헤더 "사유 미기록 N건" 배지 + 일괄 입력 진입점.
 *
 * 동작:
 *   - mount 시 1회 fetch.
 *   - 토스트(reason-prompt) 가 닫힐 때마다 store.refresh() 가 외부에서 호출돼
 *     count 가 갱신된다 (push 모델 — polling 없음).
 *   - count === 0 이면 노출 안함 (헤더 영역 시각 부담 최소화).
 *   - count > 0 이면 button 으로 표시 — 클릭 시 일괄 입력 모달 열림.
 *
 * 왜 button 으로:
 *   기존 `<span role="status">` 는 알림만 가능하고 행위 진입점이 없었다.
 *   미기록이 누적되는 동안 운영자가 소급해서 사유를 입력할 수 있는 경로를
 *   주려면 click target 이 필요하다. role="status" 는 button 이 가져가
 *   스크린리더 알림(aria-live polite) 의도를 유지.
 *
 * 컬러:
 *   var(--color-warning) — Tailwind arbitrary value 로 디자인 시스템 토큰 준수.
 *   raw hex 금지 (verify-pwc-design 통과 목적).
 */
import { useEffect, useState } from "react";

import { useMissingReasonsStore } from "../store/missingReasonsStore";
import { MissingReasonsBulkModal } from "./MissingReasonsBulkModal";

export function MissingReasonsBadge() {
  const count = useMissingReasonsStore((s) => s.count);
  const refresh = useMissingReasonsStore((s) => s.refresh);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // count = 0 이어도 모달이 열려 있는 동안엔 사용자가 마지막 행을 처리하는
  // 도중 배지가 사라지지 않게 모달을 그대로 유지 — onClose 가 호출되면
  // 모달 자체 unmount 되므로 추가 cleanup 불필요.
  if (count <= 0 && !modalOpen) return null;

  return (
    <>
      {count > 0 && (
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          // aria-live="polite" 는 신규 미기록 발생 시 스크린리더가 비강제로
          // 알리도록 한다. role="status" 를 쓰지 않는 이유: button 의 native
          // ARIA role 을 덮어쓰면 accessibility tree 가 이 element 를
          // interactive 로 인식하지 않아 키보드/스크린리더 사용자가 클릭
          // target 으로 발견하지 못한다 (live announcement 와 interactive
          // 의미가 충돌하면 interactive 가 우선되어야 함).
          aria-live="polite"
          aria-label={`사유 미기록 ${count}건 — 클릭해서 일괄 입력`}
          title="클릭하여 미기록 변경 세트에 사유를 일괄 입력합니다"
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-small font-medium border cursor-pointer bg-[color:var(--color-bg-elevated)] border-[color:var(--color-warning)] text-[color:var(--color-warning)] hover:bg-[color:var(--color-warning-bg,var(--color-bg-elevated))]"
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
        </button>
      )}
      <MissingReasonsBulkModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
      />
    </>
  );
}
