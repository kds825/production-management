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
        이 장비·SQ 조합의 <b>실제 값</b> 을 편집합니다. 값이 있으면 이 값이 우선
        적용되고, 없으면 <code>/master/constraints</code> 의 공정 기본값
        (4-1/4-2) 이 fallback 으로 사용됩니다.
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
                  <NumberCell
                    value={Number(s.setup_spec_min ?? 0)}
                    onSave={(v) => saveCell(s.speed_id, "setup_spec_min", v)}
                  />
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
