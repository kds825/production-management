"use client";

import { useEffect, useState } from "react";
import { EDITABLE_CONSTRAINTS, ParamEditor } from "./components/ParamEditor";
import { DriftBanner } from "./components/DriftBanner";
import { SaveModal } from "./components/SaveModal";
import { HistoryTab } from "./components/HistoryTab";

interface Constraint {
  constraint_id: string;
  constraint_name: string;
  category: string;
  is_enabled: boolean;
  priority: number;
  impact_level: string;
  params_json: Record<string, number>;
  applicable_processes: string[];
  implementation_type: string;
  notes: string | null;
}

type Tab = "params" | "toggle" | "history";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

const IMPACT_COLORS: Record<string, string> = {
  "★★★": "bg-red-100 text-red-800",
  "★★": "bg-yellow-100 text-yellow-800",
  "★": "bg-gray-100 text-gray-600",
};

export default function ConstraintsPage() {
  const [constraints, setConstraints] = useState<Constraint[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("params");
  const [editedParams, setEditedParams] = useState<
    Record<string, Record<string, number>>
  >({});
  const [modalOpen, setModalOpen] = useState(false);
  const [driftRefresh, setDriftRefresh] = useState(0);

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

  const onParamsChange = (id: string, params: Record<string, number>) => {
    setEditedParams((prev) => ({ ...prev, [id]: params }));
  };

  const onRestoreDefault = (id: string) => {
    const spec = EDITABLE_CONSTRAINTS.find((c) => c.constraint_id === id);
    if (!spec) return;
    const defaults: Record<string, number> = {};
    for (const p of spec.params) defaults[p.key] = p.seedDefault;
    setEditedParams((prev) => ({ ...prev, [id]: defaults }));
  };

  // Group (for toggle tab)
  const grouped = constraints.reduce<Record<string, Constraint[]>>((acc, c) => {
    const cat = c.category || "기타";
    (acc[cat] ??= []).push(c);
    return acc;
  }, {});

  if (loading) return <div className="p-8 text-gray-400">로딩 중...</div>;

  const enabledCount = constraints.filter((c) => c.is_enabled).length;

  const editorConstraints = constraints
    .filter((c) =>
      EDITABLE_CONSTRAINTS.some((e) => e.constraint_id === c.constraint_id),
    )
    .map((c) => ({
      constraint_id: c.constraint_id,
      params_json: c.params_json || {},
    }));

  const hasEdits = Object.values(editedParams).some(
    (p) => Object.keys(p).length > 0,
  );

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">제약 파라미터</h1>
          <p className="text-sm text-gray-500">
            스케줄러 셋업/교체 시간 등 파라미터 편집 · {enabledCount}/
            {constraints.length}개 활성화
          </p>
        </div>
      </div>

      <DriftBanner refreshKey={driftRefresh} />

      {/* Tabs */}
      <div className="mb-4 flex gap-1 border-b">
        {(["params", "toggle", "history"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm ${
              tab === t
                ? "border-b-2 border-blue-600 font-semibold text-blue-600"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {t === "params"
              ? "파라미터"
              : t === "toggle"
                ? "제약 on/off"
                : "변경 이력"}
          </button>
        ))}
      </div>

      {/* Params tab */}
      {tab === "params" && (
        <>
          <p className="mb-3 rounded border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
            공정별 기본값입니다. 특정 장비·SQ 조합에 실제 값이 필요하면{" "}
            <a href="/master/speed" className="text-blue-600 hover:underline">
              선속 마스터
            </a>{" "}
            에서 편집하세요 (row 값이 있으면 여기 기본값보다 우선 적용됩니다).
          </p>
          <ParamEditor
            constraints={editorConstraints}
            editedParams={editedParams}
            onParamsChange={onParamsChange}
            onRestoreDefault={onRestoreDefault}
          />
          {hasEdits && (
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                onClick={() => setEditedParams({})}
                className="rounded border border-gray-300 px-4 py-2 text-sm text-gray-600"
              >
                변경 취소
              </button>
              <button
                onClick={() => setModalOpen(true)}
                className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
              >
                저장
              </button>
            </div>
          )}
        </>
      )}

      {/* Toggle tab (기존 UI 보존) */}
      {tab === "toggle" && (
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
      )}

      {tab === "history" && <HistoryTab />}

      <SaveModal
        open={modalOpen}
        edits={editedParams}
        onClose={() => setModalOpen(false)}
        onSaved={() => {
          setEditedParams({});
          setDriftRefresh(Date.now());
          // Why: constraints list 도 갱신되어야 이후 편집 시 '수정됨' 배지가 정확
          fetch(`${API}/constraints`)
            .then((r) => r.json())
            .then((data) => {
              setConstraints(
                Array.isArray(data) ? data : data.constraints || [],
              );
            })
            .catch(() => {});
        }}
      />
    </div>
  );
}
