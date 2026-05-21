"use client";

/**
 * GlossaryModal — DecisionConstraintsModal 의 헤더 "용어집" 버튼이 여는
 * 도메인 용어 카탈로그. 카테고리별로 그룹화된 constraint_id / 이름 / 설명.
 *
 * 사용처:
 *   - DecisionConstraintsModal 안의 sub-modal — 중첩 modal stack 으로
 *     관리. 부모가 useState 로 open 상태 보유, 자식이 onClose 받음.
 *
 * 왜 별도 페이지가 아닌가:
 *   - 의사결정 흐름 중간에 빠르게 참조 후 복귀하는 패턴이라 modal 이 자연.
 *   - /glossary 페이지로 빼면 router push + 컨텍스트 손실 + 좌측 사이드바
 *     메뉴 추가가 필요해 simplicity 원칙 위배.
 *
 * 데이터 source: constraintGlossary.ts (frontend 상수). backend
 * ConstraintConfig.notes 가 채워지면 그쪽으로 이관 검토.
 */

import {
  CATEGORY_INFO,
  CONSTRAINT_DESCRIPTIONS,
  categoryPrefix,
} from "./constraintGlossary";

interface GlossaryModalProps {
  isOpen: boolean;
  onClose: () => void;
}

/**
 * CONSTRAINT_DESCRIPTIONS 의 키를 prefix 기준으로 묶고 prefix 숫자순 정렬.
 * pure 함수라 컴포넌트 외부에 둬 매 render reallocation 회피.
 */
const GROUPED_CONSTRAINTS: { prefix: string; ids: string[] }[] = (() => {
  const map = new Map<string, string[]>();
  for (const id of Object.keys(CONSTRAINT_DESCRIPTIONS)) {
    const prefix = categoryPrefix(id);
    const bucket = map.get(prefix);
    if (bucket) bucket.push(id);
    else map.set(prefix, [id]);
  }
  return [...map.entries()]
    .sort(([a], [b]) => {
      const na = Number(a);
      const nb = Number(b);
      if (Number.isNaN(na) && Number.isNaN(nb)) return a.localeCompare(b);
      if (Number.isNaN(na)) return 1;
      if (Number.isNaN(nb)) return -1;
      return na - nb;
    })
    .map(([prefix, ids]) => ({
      prefix,
      ids: ids.sort((a, b) => {
        // "5-1", "5-2", ..., "5-10" 의 sub-id 숫자순 정렬.
        const sa = Number(a.split("-")[1] ?? "0");
        const sb = Number(b.split("-")[1] ?? "0");
        return sa - sb;
      }),
    }));
})();

export function GlossaryModal({ isOpen, onClose }: GlossaryModalProps) {
  if (!isOpen) return null;

  return (
    <div
      data-testid="glossary-modal"
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-label="제약 용어집"
    >
      <div
        className="bg-white rounded-lg shadow-xl w-[720px] max-w-[92vw] max-h-[80vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between border-b px-6 py-4 shrink-0">
          <div>
            <h2 className="text-lg font-semibold">용어집</h2>
            <p className="text-xs text-gray-500">
              스케줄링 제약 카탈로그 — 카테고리별 정렬
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="닫기"
          >
            &times;
          </button>
        </div>

        {/* 본문 */}
        <div className="flex-1 overflow-auto px-6 py-5 flex flex-col gap-5">
          {GROUPED_CONSTRAINTS.map((group) => {
            const info = CATEGORY_INFO[group.prefix];
            return (
              <section key={group.prefix}>
                <div className="border-b border-gray-200 pb-2 mb-2">
                  <div className="text-xs font-semibold text-gray-700 uppercase">
                    #{group.prefix} · {info?.label ?? "기타"}
                  </div>
                  {info?.description && (
                    <div className="text-xs text-gray-500 mt-0.5">
                      {info.description}
                    </div>
                  )}
                </div>
                <ul className="flex flex-col gap-1.5">
                  {group.ids.map((id) => (
                    <li
                      key={id}
                      className="flex items-baseline gap-2 text-xs leading-relaxed"
                    >
                      <span className="font-mono text-tiny text-gray-500 w-10 shrink-0">
                        #{id}
                      </span>
                      <span className="text-gray-700">
                        {CONSTRAINT_DESCRIPTIONS[id]}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>

        {/* 푸터 */}
        <div className="flex justify-end gap-2 border-t px-6 py-3 shrink-0 bg-gray-50">
          <button
            type="button"
            onClick={onClose}
            className="text-xs font-medium px-4 py-1.5 rounded border border-gray-300 hover:bg-white"
          >
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}
