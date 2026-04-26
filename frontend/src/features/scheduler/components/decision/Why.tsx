/**
 * ❶ Why — 적합성 자연어 줄 (anchor 5-1, 10-3, 3-3, 4-2_save / warn ...).
 *
 * 백엔드 `phrasing.adequacy_line` / `impact_line` 결과 list. 운영자는 본 섹션
 * 한 줄씩 "왜 이 설비/시간/색상" 인지 자연어로 파악.
 */

import type { DecisionLine } from "./decisionCardTypes";
import { DecisionLineRow } from "./DecisionLineRow";

interface Props {
  lines: DecisionLine[];
  defaultExpanded: boolean;
  onFeedback?: (anchor: string) => void;
}

export function Why({ lines, defaultExpanded, onFeedback }: Props) {
  if (lines.length === 0) return null;
  return (
    <details
      className="border-t border-pwc-gray-200 px-4 py-3"
      open={defaultExpanded}
    >
      <summary className="text-pwc-subTitle2 cursor-pointer list-none flex items-center justify-between">
        <span>❶ 왜 이 결정인가요?</span>
        <span className="text-pwc-caption text-pwc-gray-500" aria-hidden>
          ▼
        </span>
      </summary>
      <ul className="mt-3 space-y-2 list-none pl-0">
        {lines.map((line) => (
          <DecisionLineRow
            key={line.anchor}
            line={line}
            onFeedback={onFeedback}
          />
        ))}
      </ul>
    </details>
  );
}
