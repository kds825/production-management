"use client";

// Why: 두 베이스라인 사이의 params_json 차이를 표 형태로 시각화. value 가 null
// (= 한쪽에만 존재하는 키)인 경우 시각적으로 구분되도록 dash 표시.

export interface DiffRow {
  constraint_id: string;
  field: string;
  // value_a / value_b 는 베이스라인 시점 params_json 값 — 보통 number 이지만
  // 향후 문자열/불리언 도 들어올 수 있어 unknown 으로 받는다.
  value_a: unknown;
  value_b: unknown;
}

interface VersionDiffProps {
  rows: DiffRow[];
  labelA?: string;
  labelB?: string;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function VersionDiff({ rows, labelA, labelB }: VersionDiffProps) {
  if (rows.length === 0) {
    return (
      <div className="rounded border border-dashed p-8 text-center text-sm text-gray-400">
        두 베이스라인 사이에 차이가 없습니다.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded border border-gray-200">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-xs text-gray-500">
          <tr>
            <th className="px-3 py-2 text-left font-medium">제약</th>
            <th className="px-3 py-2 text-left font-medium">필드</th>
            <th className="px-3 py-2 text-right font-medium">
              {labelA ?? "A"}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {labelB ?? "B"}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((r, i) => (
            <tr
              key={`${r.constraint_id}-${r.field}-${i}`}
              className="hover:bg-gray-50"
            >
              <td className="px-3 py-2 font-mono text-xs text-gray-500">
                {r.constraint_id}
              </td>
              <td className="px-3 py-2 text-gray-700">{r.field}</td>
              <td className="px-3 py-2 text-right font-mono text-xs">
                <span
                  className={
                    r.value_a == null ? "text-gray-300" : "text-red-600"
                  }
                >
                  {formatValue(r.value_a)}
                </span>
              </td>
              <td className="px-3 py-2 text-right font-mono text-xs">
                <span
                  className={
                    r.value_b == null ? "text-gray-300" : "text-green-700"
                  }
                >
                  {formatValue(r.value_b)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
