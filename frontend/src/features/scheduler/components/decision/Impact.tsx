/**
 * ❷ Impact — 4종 임팩트 셀 (color_change / spec_change / due_slack / wip_loss).
 *
 * 각 cell.severity 는 백엔드 phrasing.severity_for(kind, value) 결과. 운영자
 * 50건 카드 봐도 같은 색 = 같은 종류 신호로 학습 (UI review §B.6).
 */

import type { ImpactBlock } from "./decisionCardTypes";
import { SEVERITY_BG, SEVERITY_MARK, SEVERITY_TEXT } from "./severityTokens";

interface Props {
  block: ImpactBlock;
  defaultExpanded: boolean;
}

export function Impact({ block, defaultExpanded }: Props) {
  if (block.cells.length === 0) return null;
  return (
    <details
      className="border-t border-pwc-gray-200 px-4 py-3"
      open={defaultExpanded}
    >
      <summary className="text-pwc-subTitle2 cursor-pointer list-none flex items-center justify-between">
        <span>❷ 어떤 영향이 있나요?</span>
        <span className="text-pwc-caption text-pwc-gray-500" aria-hidden>
          ▼
        </span>
      </summary>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {block.cells.map((cell) => (
          <div
            key={cell.kind || cell.label}
            className={`${SEVERITY_BG[cell.severity]} rounded px-3 py-2 flex items-baseline justify-between`}
          >
            <span className="text-pwc-subBody text-pwc-gray-600">
              {cell.label}
            </span>
            <span
              className={`${SEVERITY_TEXT[cell.severity]} text-pwc-subTitle2 ml-2`}
            >
              {SEVERITY_MARK[cell.severity]} {cell.value}
            </span>
          </div>
        ))}
      </div>
      {block.duration_breakdown.length > 0 ? (
        <details className="mt-3">
          <summary className="text-pwc-caption text-pwc-gray-500 cursor-pointer">
            소요시간이 어떻게 산정됐나요?
          </summary>
          <table className="mt-2 w-full text-pwc-subBody">
            <tbody>
              {block.duration_breakdown.map((row, i) => {
                const label = String(row.label ?? "");
                const value = String(row.value ?? "");
                return (
                  <tr key={i} className="border-t border-pwc-gray-200">
                    <td className="py-1">{label}</td>
                    <td className="py-1 text-right text-pwc-gray-600">
                      {value}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      ) : null}
    </details>
  );
}
