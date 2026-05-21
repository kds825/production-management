"use client";

import { useState } from "react";
import Image from "next/image";
import { WipUploadSection } from "@/features/plan-register/components/wip-upload/WipUploadSection";
import { ErpUploadSection } from "@/features/plan-register/components/erp-upload/ErpUploadSection";
import type { WipFile, UploadMode } from "@/features/plan-register/types";

function getKstToday(): string {
  const kst = new Date(
    new Date().toLocaleString("en-US", { timeZone: "Asia/Seoul" }),
  );
  const y = kst.getFullYear();
  const m = String(kst.getMonth() + 1).padStart(2, "0");
  const d = String(kst.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export default function PlanRegisterPage() {
  // baseDate 초기값: SSR 은 오늘(결정적), client 는 localStorage 의 사용자
  // 선택값이 있으면 그것, 없으면 오늘.
  // - useEffect 로 mount 시점에 today 로 무조건 덮어쓰는 패턴은 쓰지 않는다.
  //   사용자가 4/1 등 명시적으로 고른 값을 다른 페이지를 거쳐 돌아왔을 때
  //   reset 시켜 Stage2 자동배열이 today 로 돌아가는 회귀가 있었기 때문.
  //   (사용자 보고: 2026-05-21, 4/1 선택했는데 스케줄이 today 부터 시작.)
  // - lazy init 의 SSR↔client 차이는 input[value] 한 칸에 한정되므로
  //   suppressHydrationWarning 으로 허용.
  const [baseDate, setBaseDate] = useState<string>(() => {
    if (typeof window === "undefined") return getKstToday();
    const stored = window.localStorage.getItem("plan_base_date");
    if (stored && stored.length === 8) {
      return `${stored.slice(0, 4)}-${stored.slice(4, 6)}-${stored.slice(6, 8)}`;
    }
    return getKstToday();
  });
  const [wipFile, setWipFile] = useState<WipFile | null>(null);
  // incremental 모드 선택 시 WIP 섹션을 흐리게 처리하기 위해 모드를 상위에서 관리
  const [erpUploadMode, setErpUploadMode] = useState<UploadMode>("full");

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ backgroundColor: "var(--color-bg-muted)" }}
    >
      {/* 헤더 — KBI 로고 + 페이지 제목 */}
      <header className="h-14 bg-white border-b border-gray-200 flex items-center px-6 sticky top-0 z-50 shrink-0">
        <div className="flex items-center gap-3">
          <Image
            src="/kbi-group-logo.jpg"
            alt="KBI GROUP"
            width={72}
            height={36}
            className="object-contain"
          />
          <div className="h-6 w-px bg-gray-200" />
          <h1
            className="text-sm font-semibold"
            style={{ color: "var(--kbi-brown)", letterSpacing: "-0.02em" }}
          >
            생산계획등록
          </h1>
        </div>
      </header>

      {/* 본문 */}
      <div className="flex-1 overflow-auto px-6 py-6 flex flex-col gap-0">
        {/* 계획 기준일자 */}
        <section className="mb-6">
          <h3
            className="text-sm font-semibold mb-1"
            style={{ color: "var(--color-text-primary)" }}
          >
            1. 계획 기준일자
          </h3>
          <p className="text-xs text-gray-500 mb-3">
            생산계획의 시작 기준일을 선택하세요 (기본: 오늘)
          </p>
          <input
            type="date"
            value={baseDate}
            // SSR(오늘) ↔ client(localStorage 의 사용자 선택값) 가 다를 수
            // 있다 — 사용자 선택 복원이 의도된 동작이라 warning 만 숨긴다.
            suppressHydrationWarning
            onChange={(e) => {
              setBaseDate(e.target.value);
              if (typeof window !== "undefined") {
                localStorage.setItem(
                  "plan_base_date",
                  e.target.value.replace(/-/g, ""),
                );
              }
            }}
            className="rounded-md px-3 py-2 text-sm border"
            style={{
              borderColor: "var(--neutral-300)",
              color: "var(--color-text-primary)",
              outline: "none",
            }}
          />
        </section>

        {/* incremental 모드에서는 WIP 섹션을 반투명하게 처리해 비활성 상태를 시각화 */}
        <div
          style={{
            opacity: erpUploadMode === "incremental" ? 0.5 : 1,
            transition: "opacity 150ms ease",
            pointerEvents: erpUploadMode === "incremental" ? "none" : undefined,
          }}
        >
          <WipUploadSection wipFile={wipFile} setWipFile={setWipFile} />
        </div>
        <ErpUploadSection
          wipFile={wipFile}
          baseDate={baseDate}
          onBaseDateChange={(d) => {
            setBaseDate(d);
            if (typeof window !== "undefined") {
              localStorage.setItem("plan_base_date", d.replace(/-/g, ""));
            }
          }}
          onUploadModeChange={setErpUploadMode}
        />
      </div>
    </div>
  );
}
