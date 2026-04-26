"use client";

/**
 * BellIcon — topbar 운영자 unread feedback resolution count (UI review §12).
 *
 * 60s polling. 운영자가 fixed/wontfix 처리된 의견 건수를 헤더에서 즉시 확인.
 * 클릭 → /operator/my-feedback view 진입.
 */

import { useEffect, useState } from "react";

import { apiFetch } from "@/shared/api/client";

const POLL_INTERVAL_MS = 60_000;

function readOperatorId(): string {
  if (typeof window === "undefined") return "anonymous";
  return window.localStorage.getItem("kbi.operator_id") || "anonymous";
}

export function BellIcon() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const operatorId = readOperatorId();

    const tick = async () => {
      try {
        const data = await apiFetch<{ unread: number }>(
          `/decision-feedback/unread-count?operator_id=${encodeURIComponent(operatorId)}`,
        );
        if (!cancelled) setCount(data.unread);
      } catch {
        // bell 폴링 실패는 silent — 운영자 핵심 path 가 아님
      }
    };
    tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <a
      href="/operator/my-feedback"
      className="relative inline-flex items-center justify-center w-9 h-9 rounded-full hover:bg-pwc-gray-100"
      aria-label={
        count > 0 ? `처리된 의견 ${count}건 새로 있음` : "처리된 의견 알림"
      }
    >
      <span aria-hidden className="text-pwc-subTitle2">
        🔔
      </span>
      {count > 0 ? (
        <span
          className="absolute -top-1 -right-1 bg-pwc-status-danger-text text-pwc-text-inverse text-pwc-badge rounded-full min-w-[18px] h-[18px] px-1 inline-flex items-center justify-center"
          aria-hidden
        >
          {count > 99 ? "99+" : count}
        </span>
      ) : null}
    </a>
  );
}
