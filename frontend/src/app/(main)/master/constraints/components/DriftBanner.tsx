"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface DriftBannerProps {
  /** 저장 직후 강제 재조회 트리거 (Date.now() 같은 값 변경 시 refetch) */
  refreshKey?: number;
}

export function DriftBanner({ refreshKey }: DriftBannerProps) {
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/constraints/drift-status`)
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) setDirty(Boolean(data?.dirty));
      })
      .catch(() => {
        if (!cancelled) setDirty(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (!dirty) return null;

  return (
    <div className="mb-4 flex items-center gap-3 rounded border border-yellow-400 bg-yellow-50 px-4 py-2 text-sm">
      <span aria-hidden>⚠️</span>
      <span className="flex-1 text-yellow-900">
        저장된 변경이 아직 스케줄에 반영되지 않았습니다. 작업지시서 업데이트에서
        재실행해야 새 계획에 적용됩니다.
      </span>
      <a
        href="/plan-pipeline"
        className="whitespace-nowrap text-blue-600 hover:underline"
      >
        작업지시서 업데이트로 이동 →
      </a>
    </div>
  );
}
