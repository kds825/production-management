"use client";

import { useEffect, useState } from "react";

interface SpeedRecord {
  speed_id: number;
  equipment_code: string;
  product_type: string;
  cross_section: number;
  line_speed_mpm: number;
  setup_spec_min: number;
  setup_color_min: number;
}

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export default function SpeedPage() {
  const [speeds, setSpeeds] = useState<SpeedRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    fetch(`${API}/master/speed_master`)
      .then((r) => r.json())
      .then((data) => {
        setSpeeds(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

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

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-2 text-left">설비코드</th>
              <th className="px-3 py-2 text-left">제품유형</th>
              <th className="px-3 py-2 text-right">SQ</th>
              <th className="px-3 py-2 text-right">선속(m/min)</th>
              <th className="px-3 py-2 text-right">규격교체(분)</th>
              <th className="px-3 py-2 text-right">색상교체(분)</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {filtered.map((s) => (
              <tr key={s.speed_id} className="hover:bg-gray-50">
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
                <td className="px-3 py-2 text-right">
                  {s.setup_spec_min || "-"}
                </td>
                <td className="px-3 py-2 text-right">
                  {s.setup_color_min || "-"}
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
