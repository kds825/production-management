/**
 * 자연어 줄 row — anchor + natural + severity badge.
 *
 * onFeedback 콜백: ⚠ 마크 클릭 시 line_anchor 와 함께 호출 → 부모가 dialog
 * 띄움. v2 MVP 는 prop 만 정의 (Step 6-MVP 에서 wire-up).
 */

import type { DecisionLine } from "./decisionCardTypes";
import { SEVERITY_BG, SEVERITY_MARK, SEVERITY_TEXT } from "./severityTokens";

interface Props {
  line: DecisionLine;
  onFeedback?: (anchor: string) => void;
}

export function DecisionLineRow({ line, onFeedback }: Props) {
  const showFlag = line.severity === "warn" || line.severity === "fail";
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
