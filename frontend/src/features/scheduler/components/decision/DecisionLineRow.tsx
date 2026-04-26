/**
 * 자연어 줄 row — anchor + natural + severity badge + Tier 2 ⓘ popover.
 *
 * onFeedback 콜백: ⚠ 마크 클릭 시 line_anchor 와 함께 호출 → 부모가 dialog
 * 띄움. line.detail 이 있으면 ⓘ 호버 popover 노출 (Step 5).
 */

import type { DecisionLine } from "./decisionCardTypes";
import { SEVERITY_BG, SEVERITY_MARK, SEVERITY_TEXT } from "./severityTokens";

interface Props {
  line: DecisionLine;
  onFeedback?: (anchor: string) => void;
}

export function DecisionLineRow({ line, onFeedback }: Props) {
  const showFlag = line.severity === "warn" || line.severity === "fail";
  const hasDetail = Object.keys(line.detail || {}).length > 0;
  return (
    <li className="flex items-start gap-2">
      <span
        className={`${SEVERITY_BG[line.severity]} ${SEVERITY_TEXT[line.severity]} rounded px-1.5 py-0.5 text-pwc-badge shrink-0`}
        aria-label={`severity ${line.severity}`}
      >
        {SEVERITY_MARK[line.severity]}
      </span>
      <span className="text-pwc-body flex-1">
        {line.natural}
        {line.constraint_id ? (
          <span className="text-pwc-caption text-pwc-gray-500 ml-2 font-mono">
            #{line.constraint_id}
          </span>
        ) : null}
        {hasDetail ? (
          <span
            className="text-pwc-caption text-pwc-status-info-text ml-1 cursor-help relative group"
            tabIndex={0}
            aria-label="추가 설명"
          >
            ⓘ
            <span
              role="tooltip"
              className="invisible group-hover:visible group-focus:visible absolute left-0 top-full mt-1 z-10 bg-pwc-bg-elevated border border-pwc-gray-200 rounded shadow-md p-2 min-w-[260px] text-pwc-subBody text-pwc-gray-600 whitespace-pre-wrap"
            >
              {Object.entries(line.detail).map(([k, v]) => (
                <span key={k} className="block">
                  <strong className="text-pwc-gray-500">{k}:</strong>{" "}
                  {String(v)}
                </span>
              ))}
            </span>
          </span>
        ) : null}
      </span>
      {showFlag && onFeedback ? (
        <button
          type="button"
          onClick={() => onFeedback(line.anchor)}
          className="text-pwc-caption text-pwc-status-warning-text hover:underline shrink-0"
          aria-label={`${line.natural} — 의견 보내기`}
        >
          이상해요
        </button>
      ) : null}
    </li>
  );
}
