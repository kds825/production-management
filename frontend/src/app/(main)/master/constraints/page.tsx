"use client";

import { useEffect, useState } from "react";

interface Constraint {
  constraint_id: string;
  constraint_name: string;
  category: string;
  is_enabled: boolean;
  priority: number;
  impact_level: string;
  params_json: Record<string, any>;
  applicable_processes: string[];
  implementation_type: string;
  notes: string | null;
}

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

const IMPACT_COLORS: Record<string, string> = {
  "★★★": "bg-red-100 text-red-800",
  "★★": "bg-yellow-100 text-yellow-800",
  "★": "bg-gray-100 text-gray-600",
};

export default function ConstraintsPage() {
  const [constraints, setConstraints] = useState<Constraint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/constraints`)
      .then((r) => r.json())
      .then((data) => {
        setConstraints(Array.isArray(data) ? data : data.constraints || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const toggleConstraint = async (id: string, enabled: boolean) => {
    await fetch(`${API}/constraints/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_enabled: enabled }),
    });
    setConstraints((prev) =>
      prev.map((c) =>
        c.constraint_id === id ? { ...c, is_enabled: enabled } : c,
      ),
    );
  };

  // Group by category
  const grouped = constraints.reduce<Record<string, Constraint[]>>((acc, c) => {
    const cat = c.category || "기타";
    (acc[cat] ??= []).push(c);
    return acc;
  }, {});

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  const enabledCount = constraints.filter((c) => c.is_enabled).length;

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">제약조건 관리</h1>
          <p className="text-sm text-gray-500">
            {enabledCount}/{constraints.length}개 활성화
          </p>
        </div>
      </div>

      <div className="space-y-6">
        {Object.entries(grouped).map(([category, items]) => (
          <div key={category} className="rounded-lg border border-gray-200">
            <div className="border-b bg-gray-50 px-4 py-2">
              <h3 className="text-sm font-semibold text-gray-700">
                {category}{" "}
                <span className="text-gray-400">
                  ({items.filter((c) => c.is_enabled).length}/{items.length})
                </span>
              </h3>
            </div>
            <div className="divide-y">
              {items.map((c) => (
                <div
                  key={c.constraint_id}
                  className={`flex items-center gap-4 px-4 py-3 ${
                    !c.is_enabled ? "opacity-50" : ""
                  }`}
                >
                  <label className="relative inline-flex cursor-pointer items-center">
                    <input
                      type="checkbox"
                      checked={c.is_enabled}
                      onChange={(e) =>
                        toggleConstraint(c.constraint_id, e.target.checked)
                      }
                      className="peer sr-only"
                    />
                    <div className="peer h-5 w-9 rounded-full bg-gray-300 after:absolute after:left-[2px] after:top-[2px] after:h-4 after:w-4 after:rounded-full after:bg-white after:transition-all peer-checked:bg-blue-600 peer-checked:after:translate-x-full" />
                  </label>

                  <span className="w-12 text-xs font-mono text-gray-400">
                    {c.constraint_id}
                  </span>

                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      IMPACT_COLORS[c.impact_level] || "bg-gray-100"
                    }`}
                  >
                    {c.impact_level}
                  </span>

                  <div className="flex-1">
                    <div className="text-sm font-medium">
                      {c.constraint_name}
                    </div>
                    {c.notes && (
                      <div className="text-xs text-gray-400">{c.notes}</div>
                    )}
                  </div>

                  <div className="flex gap-1">
                    {(c.applicable_processes || []).map((p: string) => (
                      <span
                        key={p}
                        className="rounded bg-blue-50 px-1.5 py-0.5 text-xs text-blue-600"
                      >
                        {p}
                      </span>
                    ))}
                  </div>

                  <span className="text-xs text-gray-400">
                    {c.implementation_type}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
