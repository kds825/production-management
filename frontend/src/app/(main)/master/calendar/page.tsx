"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export default function CalendarPage() {
  const [rules, setRules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/master/operation_calendar`)
      .then((r) => r.json())
      .then((data) => {
        setRules(data.items || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">가동 캘린더</h1>
      <div className="grid gap-4 md:grid-cols-2">
        {rules.map((r: any) => (
          <div
            key={r.calendar_id || r.rule_code}
            className="rounded-lg border p-4"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="font-mono text-sm font-bold text-blue-600">
                {r.rule_code}
              </span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs ${
                  r.working_hours === 0
                    ? "bg-red-100 text-red-600"
                    : "bg-green-100 text-green-600"
                }`}
              >
                {r.working_hours}hr
              </span>
            </div>
            <h3 className="font-medium">{r.rule_name}</h3>
            <div className="mt-2 text-sm text-gray-500">
              <p>적용: {r.day_of_week}</p>
              {r.start_time && (
                <p>
                  시간: {r.start_time} ~ {r.end_time}
                </p>
              )}
              {r.deduction_hours > 0 && <p>차감: {r.deduction_hours}hr</p>}
              {r.notes && (
                <p className="mt-1 text-xs text-gray-400">{r.notes}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
