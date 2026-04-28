"use client";

import { useEffect, useState } from "react";
import { NumberCell } from "./components/NumberCell";
import { DriftBanner } from "../constraints/components/DriftBanner";

interface SpeedRecord {
  speed_id: number;
  equipment_code: string;
  product_type: string;
  cross_section: number;
  line_speed_mpm: number;
  setup_spec_min: number;
  setup_color_min: number;
  setup_compound_min: number;
  setup_start_min: number;
  updated_at: string | null;
}

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

type SetupField =
  | "setup_spec_min"
  | "setup_color_min"
  | "setup_compound_min"
  | "setup_start_min";

/**
 * setup_spec_min 이 ConstraintConfig 4-1 로 관리되는 행인지 판정.
 * Why: 4 공정(연선/저압절연/저압시스/고압절연)은 /master/constraints 4-1 이
 * authoritative 이므로, 이 행의 setup_spec_min 편집은 스케줄러에서 무시됨.
 * 혼선 방지를 위해 UI 에서 해당 셀을 숨기고 4-1 로 이동 힌트를 노출한다.
 * 장비 prefix 로 판정: ST-*(연선), EX-*(저압절연/고압절연CV), SH-A100/A120(저압시스).
 */
function isSpecMinManagedBy41(equipment_code: string): boolean {
  if (equipment_code.startsWith("ST-")) return true;
  if (equipment_code.startsWith("EX-")) return true;
  if (equipment_code === "SH-A100" || equipment_code === "SH-A120") return true;
  return false;
}

export default function SpeedPage() {
  const [speeds, setSpeeds] = useState<SpeedRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [driftRefresh, setDriftRefresh] = useState(0);

  useEffect(() => {
    fetch(`${API}/master/speed_master`)
      .then((r) => r.json())
      .then((data) => {
        setSpeeds(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const saveCell = async (
    speed_id: number,
    field: SetupField,
    value: number,
  ) => {
    const resp = await fetch(
      `${API}/master/speed_master/${speed_id}/setup-params`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: value }),
      },
    );
    if (!resp.ok) {
      let detail = `저장 실패 (${resp.status})`;
      try {
        const body = await resp.json();
        if (body?.detail) detail = String(body.detail);
      } catch {}
      throw new Error(detail);
    }
    const updated = (await resp.json()) as SpeedRecord;
    setSpeeds((prev) =>
      prev.map((s) => (s.speed_id === speed_id ? { ...s, ...updated } : s)),
    );
    // TODO(react19-migration): saveCell 을 useCallback 으로 감싸면 룰 통과.
    // saveCell 은 handler 에서 호출되므로 Date.now() 가 render 중 실행되지 않음 (false-positive).
    // eslint-disable-next-line react-hooks/purity
    setDriftRefresh(Date.now());
  };

  const filtered = filter
    ? speeds.filter(
        (s) =>
          s.equipment_code.includes(filter) ||
          s.product_type?.includes(filter) ||
          String(s.cross_section).includes(filter),
      )
    : speeds;

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">선속 마스터</h1>
        <input
          type="text"
          placeholder="검색 (설비코드, 제품유형, SQ)"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="rounded-md border px-3 py-1.5 text-sm"
        />
      </div>

      <DriftBanner refreshKey={driftRefresh} />

      <p className="mb-3 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">
        이 장비·SQ 조합의 <b>실제 값</b> 을 편집합니다.
        연선/저압절연/저압시스/고압절연의 <b>규격교체</b> 는{" "}
        <a
          href="/master/constraints"
          className="text-blue-700 underline hover:text-blue-900"
        >
          제약 파라미터 4-1
        </a>{" "}
        에서 관리됩니다 (이 표에서 편집 불가). 색상교체 등 나머지 값은 없으면
        4-2 등이 fallback 으로 사용됩니다.
      </p>

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">설비코드</th>
              <th className="px-3 py-2 text-left">제품유형</th>
              <th className="px-3 py-2 text-right">SQ</th>
              <th className="px-3 py-2 text-right">선속(m/min)</th>
              <th className="px-3 py-2 text-right">규격교체</th>
              <th className="px-3 py-2 text-right">색상교체</th>
              <th className="px-3 py-2 text-right">컴파운드교체</th>
              <th className="px-3 py-2 text-right">시작셋업</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {filtered.map((s) => (
              <tr key={s.speed_id} className="hover:bg-gray-50/60">
                <td className="px-3 py-2 font-mono text-xs">
                  {s.equipment_code}
                </td>
                <td className="px-3 py-2">{s.product_type}</td>
                <td className="px-3 py-2 text-right">{s.cross_section}</td>
                <td
                  className={`px-3 py-2 text-right font-medium ${
                    s.line_speed_mpm ? "" : "bg-yellow-50 text-yellow-600"
                  }`}
                >
                  {s.line_speed_mpm || "미기재"}
                </td>
                <td className="px-2 py-1 text-right">
                  {isSpecMinManagedBy41(s.equipment_code) ? (
                    <a
                      href="/master/constraints"
                      title="이 공정의 규격교체는 제약 파라미터 4-1 에서 관리됩니다"
                      className="block rounded bg-gray-50 px-2 py-1 text-xs text-gray-400 hover:bg-blue-50 hover:text-blue-600"
                    >
                      4-1 에서 관리 →
                    </a>
                  ) : (
                    <NumberCell
                      value={Number(s.setup_spec_min ?? 0)}
                      onSave={(v) => saveCell(s.speed_id, "setup_spec_min", v)}
                    />
                  )}
                </td>
                <td className="px-2 py-1 text-right">
                  <NumberCell
                    value={Number(s.setup_color_min ?? 0)}
                    onSave={(v) => saveCell(s.speed_id, "setup_color_min", v)}
                  />
                </td>
                <td className="px-2 py-1 text-right">
                  <NumberCell
                    value={Number(s.setup_compound_min ?? 0)}
                    onSave={(v) =>
                      saveCell(s.speed_id, "setup_compound_min", v)
                    }
                  />
                </td>
                <td className="px-2 py-1 text-right">
                  <NumberCell
                    value={Number(s.setup_start_min ?? 0)}
                    onSave={(v) => saveCell(s.speed_id, "setup_start_min", v)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-gray-400">총 {filtered.length}건</p>
    </div>
  );
}
