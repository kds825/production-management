"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface PreviewImpact {
  affected_batch_count: number;
  total_delta_min: number;
  current_value?: number;
  new_value?: number;
  note?: string;
}

interface SaveModalProps {
  open: boolean;
  edits: Record<string, Record<string, number>>;
  onClose: () => void;
  onSaved: () => void;
}

export function SaveModal({ open, edits, onClose, onSaved }: SaveModalProps) {
  const [preview, setPreview] = useState<Record<string, PreviewImpact>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPreview({});
    setError(null);
    const fetchAll = async () => {
      const results: Record<string, PreviewImpact> = {};
      for (const [id, params] of Object.entries(edits)) {
        try {
          const resp = await fetch(`${API}/constraints/${id}/preview-impact`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_params_json: params }),
          });
          results[id] = await resp.json();
        } catch {
          results[id] = {
            affected_batch_count: 0,
            total_delta_min: 0,
            note: "프리뷰 조회 실패",
          };
        }
      }
      if (!cancelled) setPreview(results);
    };
    fetchAll();
    return () => {
      cancelled = true;
    };
  }, [open, edits]);

  if (!open) return null;

  const doSave = async (triggerStage1: boolean) => {
    setSaving(true);
    setError(null);
    try {
      for (const [id, params] of Object.entries(edits)) {
        const resp = await fetch(`${API}/constraints/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ params_json: params }),
        });
        if (!resp.ok) {
          throw new Error(`PATCH ${id} 실패: ${resp.status}`);
        }
      }
      onSaved();
      if (triggerStage1) {
        // Why: Stage1/update 화면에서 base_date 지정 후 재실행해야 하므로
        // 단순 라우팅 이동. 자동 재실행은 감사 관점에서 위험 (design review M).
        window.location.href = "/plan-pipeline";
        return;
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  };

  const totalAffected = Object.values(preview).reduce(
    (sum, p) => sum + (p.affected_batch_count ?? 0),
    0,
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
    >
      <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <h3 className="mb-3 text-lg font-bold">변경 사항 확인</h3>

        <div className="mb-4 space-y-2 text-sm">
          {Object.entries(edits).map(([id, params]) => {
            const p = preview[id];
            const entries = Object.entries(params);
            return (
              <div key={id} className="rounded border px-3 py-2">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-gray-500">{id}</span>
                  {p && (
                    <span className="text-xs text-gray-600">
                      영향 계획 배치: {p.affected_batch_count}건
                      {typeof p.total_delta_min === "number" && (
                        <>
                          {" · "}Δ: {p.total_delta_min > 0 ? "+" : ""}
                          {p.total_delta_min}분
                        </>
                      )}
                    </span>
                  )}
                </div>
                <div className="mt-1 space-y-0.5 text-xs">
                  {entries.map(([k, v]) => (
                    <div key={k}>
                      <span className="text-gray-500">{k}:</span> {v}분
                    </div>
                  ))}
                </div>
                {p?.note && (
                  <div className="mt-1 text-xs text-gray-400">{p.note}</div>
                )}
              </div>
            );
          })}
          {Object.keys(preview).length === 0 && (
            <div className="text-xs text-gray-400">영향 분석 중...</div>
          )}
        </div>

        <p className="mb-4 rounded bg-gray-50 px-3 py-2 text-xs text-gray-600">
          진행중(in_progress) 배치와 기준일자 이전 확정 배치는 변경되지
          않습니다. 앞으로의 계획 배치에만 새 값이 적용됩니다.
        </p>

        {error && (
          <div className="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex flex-col gap-2">
          <button
            disabled={saving}
            onClick={() => doSave(true)}
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {saving
              ? "저장 중..."
              : "지금 기존 계획에도 반영 (Stage1/update 이동)"}
          </button>
          <button
            disabled={saving}
            onClick={() => doSave(false)}
            className="rounded border border-blue-600 px-4 py-2 text-sm text-blue-600 hover:bg-blue-50 disabled:opacity-50"
          >
            다음 자동배열부터 적용
          </button>
          <button
            disabled={saving}
            onClick={onClose}
            className="rounded px-4 py-2 text-sm text-gray-500 hover:text-gray-700 disabled:opacity-50"
          >
            취소
          </button>
        </div>

        {totalAffected > 0 && (
          <div className="mt-3 text-center text-xs text-gray-400">
            총 {totalAffected}건의 계획 배치가 영향을 받을 수 있습니다.
          </div>
        )}
      </div>
    </div>
  );
}
