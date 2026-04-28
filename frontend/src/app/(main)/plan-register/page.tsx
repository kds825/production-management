"use client";

import { useState, useEffect } from "react";
import Image from "next/image";
import { WipUploadSection } from "@/features/plan-register/components/WipUploadSection";
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
  // baseDate 초기화: SSR 은 오늘 날짜를 반환해 서버 렌더를 결정적으로 유지하고,
  // client lazy-init 은 localStorage 에 저장된 값을 읽어 사용자 선호를 복원한다.
  // 잠재적 hydration mismatch 는 input[value] 차원에서만 발생하며
  // suppressHydrationWarning 으로 허용(사용자 입력 컨트롤이므로 자연스러움).
  // 이 패턴은 "setState-in-effect" 안티패턴을 피하기 위한 공식 대안.
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

  // 최초 방문 시 localStorage 기본값을 오늘 날짜로 채워둔다(외부 시스템 동기화만,
  // setState 호출 없음 — cascading render 방지).
  useEffect(() => {
    if (!localStorage.getItem("plan_base_date")) {
      localStorage.setItem("plan_base_date", getKstToday().replace(/-/g, ""));
    }
  }, []);

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
            // lazy-init 에서 localStorage 값을 읽으므로 SSR(오늘) ↔ client(저장값)
            // 가 다를 수 있다. 사용자 선호 복원 용도라 첫 페인트에서 잠시 다른
            // 값이 보이는 것은 의도된 동작이며 hydration warning 만 가린다.
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
          onUploadModeChange={setErpUploadMode}
        />
      </div>
    </div>
  );
}
