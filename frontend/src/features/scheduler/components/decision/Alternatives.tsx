/**
 * ❻ Alternatives — 탈락 후보 list.
 *
 * 백엔드 phrasing.filter_out_reason(code, params) 결과. 운영자가 "왜 다른
 * 설비/시간이 안 됐나?" 답을 1줄씩 자연어로 본다.
 */

import type { Alternative } from "./decisionCardTypes";
import { SEVERITY_BG, SEVERITY_MARK, SEVERITY_TEXT } from "./severityTokens";

interface Props {
  rows: Alternative[];
  defaultExpanded: boolean;
}

export function Alternatives({ rows, defaultExpanded }: Props) {
  if (rows.length === 0) return null;
  return (
    <details
      className="border-t border-pwc-gray-200 px-4 py-3"
      open={defaultExpanded}
    >
      <summary className="text-pwc-subTitle2 cursor-pointer list-none flex items-center justify-between">
        <span>❻ 다른 후보는 왜 안 됐나요?</span>
        <span className="text-pwc-caption text-pwc-gray-500" aria-hidden>
          ▼
        </span>
      </summary>
      <ul className="mt-2 space-y-2 list-none pl-0">
        {rows.map((row, i) => (
          <li
            key={`${row.candidate_label}-${i}`}
            className="flex items-start gap-2"
          >
            <span
              className={`${SEVERITY_BG[row.severity]} ${SEVERITY_TEXT[row.severity]} rounded px-1.5 py-0.5 text-pwc-badge shrink-0`}
            >
              {SEVERITY_MARK[row.severity]}
            </span>
            <div className="flex-1">
              <div className="text-pwc-body">{row.candidate_label}</div>
              <div className="text-pwc-subBody text-pwc-gray-600">
                {row.rejected_reason}
                {row.code ? (
                  <span className="font-mono text-pwc-caption text-pwc-gray-500 ml-2">
                    #{row.code}
                  </span>
                ) : null}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </details>
  );
}
