"use client";

import { useEffect, useRef, useState } from "react";

type Status = "idle" | "saving" | "success" | "error";

interface NumberCellProps {
  value: number;
  onSave: (next: number) => Promise<void>;
  min?: number;
  step?: number;
}

function formatMin(m: number): string {
  if (!Number.isFinite(m) || m <= 0) return "0분";
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h === 0) return `${mm}분`;
  if (mm === 0) return `${h}시간`;
  return `${h}시간 ${mm}분`;
}

/**
 * 인라인 숫자 편집 셀 — onBlur autosave + 성공/실패 피드백 + 시간 병기.
 *
 * Why: 같은 로직이 셋업 4컬럼에 반복되므로 별도 컴포넌트로 추출.
 * 저장 실패 시 원래 값으로 복원해 사용자 데이터 손실 방지.
 */
export function NumberCell({
  value,
  onSave,
  min = 0,
  step = 1,
}: NumberCellProps) {
  const [draft, setDraft] = useState<string>(String(value));
  const [status, setStatus] = useState<Status>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  useEffect(() => {
    return () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, []);

  const commit = async () => {
    const parsed = Number(draft);
    if (!Number.isFinite(parsed) || parsed < min) {
      setErrorMsg(`${min} 이상의 숫자를 입력하세요`);
      setStatus("error");
      setDraft(String(value));
      return;
    }
    if (parsed === value) {
      setStatus("idle");
      setErrorMsg(null);
      return;
    }

    setStatus("saving");
    setErrorMsg(null);
    try {
      await onSave(parsed);
      setStatus("success");
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setStatus("idle"), 1500);
    } catch (e) {
      setStatus("error");
      setErrorMsg(e instanceof Error ? e.message : "저장 실패");
      setDraft(String(value));
    }
  };

  const parsed = Number(draft);
  const previewMin = Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;

  const bg =
    status === "success"
      ? "bg-green-50"
      : status === "error"
        ? "bg-red-50"
        : "";
  const border = status === "error" ? "border-red-500" : "border-gray-300";

  return (
    <div
      className={`flex flex-col items-end gap-0.5 rounded px-1 py-0.5 ${bg}`}
    >
      <div className="flex items-center gap-1">
        <input
          type="number"
          min={min}
          step={step}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            if (e.key === "Escape") {
              setDraft(String(value));
              setErrorMsg(null);
              setStatus("idle");
              (e.target as HTMLInputElement).blur();
            }
          }}
          disabled={status === "saving"}
          className={`w-20 rounded border px-2 py-0.5 text-right text-sm ${border}`}
        />
        {status === "saving" && (
          <span className="text-xs text-gray-400">저장중…</span>
        )}
        {status === "success" && (
          <span className="text-xs text-green-600">✓</span>
        )}
      </div>
      <span className="text-[10px] text-gray-400">
        ({formatMin(previewMin)})
      </span>
      {errorMsg && <span className="text-[10px] text-red-600">{errorMsg}</span>}
    </div>
  );
}
