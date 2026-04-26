/**
 * ❺ BundleCompare — 본 묶음 vs 인접 cluster N=5.
 *
 * 백엔드 `bundle_alternative.compare_bundles` post-hoc rebuild 결과. 본 묶음은
 * is_chosen=true 로 highlight, alternatives 는 score 기준 정렬.
 */

import type { BundleAlternative } from "./decisionCardTypes";

interface Props {
  rows: BundleAlternative[];
  defaultExpanded: boolean;
}

export function BundleCompare({ rows, defaultExpanded }: Props) {
  if (rows.length === 0) return null;
  return (
    <details
      className="border-t border-pwc-gray-200 px-4 py-3"
      open={defaultExpanded}
    >
      <summary className="text-pwc-subTitle2 cursor-pointer list-none flex items-center justify-between">
        <span>❺ 묶음 비교</span>
        <span className="text-pwc-caption text-pwc-gray-500" aria-hidden>
          ▼
        </span>
      </summary>
      <table className="mt-2 w-full text-pwc-subBody">
        <thead>
          <tr className="text-pwc-caption text-pwc-gray-500">
            <th className="text-left py-1">묶음</th>
            <th className="text-right py-1">색상교체</th>
            <th className="text-right py-1">규격교체</th>
            <th className="text-right py-1">소요</th>
            <th className="text-right py-1">점수</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={`${row.label}-${i}`}
              className={`border-t border-pwc-gray-200 ${
                row.is_chosen ? "bg-pwc-primary-100 font-medium" : ""
              }`}
            >
              <td className="py-1">
                {row.label}
                {row.is_chosen ? (
                  <span className="text-pwc-badge text-pwc-primary-500 ml-1">
                    ◀ 채택
                  </span>
                ) : null}
              </td>
              <td className="text-right py-1 font-mono">
                {row.color_change_min}분
              </td>
              <td className="text-right py-1 font-mono">
                {row.spec_change_min}분
              </td>
              <td className="text-right py-1 font-mono">
                {row.duration_min}분
              </td>
              <td className="text-right py-1 font-mono">
                {row.score.toFixed(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows[0]?.rationale ? (
        <p className="text-pwc-caption text-pwc-gray-500 mt-2">
          {rows[0].rationale}
        </p>
      ) : null}
    </details>
  );
}
