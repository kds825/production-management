"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface RoutingRow {
  routing_code: string;
  routing_name: string;
  process_1?: string | null;
  process_2?: string | null;
  process_3?: string | null;
  process_4?: string | null;
  process_5?: string | null;
  process_6?: string | null;
}

export default function RoutingPage() {
  const [routings, setRoutings] = useState<RoutingRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/master/process_routing`)
      .then((r) => r.json())
      .then((data) => {
        setRoutings(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">공정 라우팅</h1>
      <div className="space-y-4">
        {routings.map((r) => {
          const processes: string[] = [
            r.process_1,
            r.process_2,
            r.process_3,
            r.process_4,
            r.process_5,
            r.process_6,
          ].filter((p): p is string => Boolean(p));
          return (
            <div key={r.routing_code} className="rounded-lg border p-4">
              <div className="mb-2 flex items-center gap-2">
                <span className="font-mono text-sm font-bold text-blue-600">
                  {r.routing_code}
                </span>
                <span className="text-sm text-gray-500">{r.routing_name}</span>
              </div>
              <div className="flex items-center gap-2">
                {processes.map((p: string, i: number) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className="rounded bg-blue-100 px-3 py-1 text-sm font-medium text-blue-800">
                      {p}
                    </span>
                    {i < processes.length - 1 && (
                      <span className="text-gray-300">→</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
