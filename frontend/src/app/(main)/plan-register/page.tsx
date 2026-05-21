"use client";

import { useState, useEffect } from "react";
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
  // baseDate 초기값은 항상 오늘. 과거 세션의 localStorage 값이 stale 하게
  // 남아 페이지가 옛 날짜로 진입하는 회귀를 막기 위해 stored 값은 무시한다.
  // 사용자가 의도적으로 다른 날짜를 고른 경우 onChange 핸들러가 localStorage
  // 를 갱신하므로 Stage2 등 다른 페이지가 같은 값을 읽는다.
  const [baseDate, setBaseDate] = useState<string>(getKstToday);
  const [wipFile, setWipFile] = useState<WipFile | null>(null);
  // incremental 모드 선택 시 WIP 섹션을 흐리게 처리하기 위해 모드를 상위에서 관리
  const [erpUploadMode, setErpUploadMode] = useState<UploadMode>("full");

  // 진입 시점에 localStorage 를 오늘 날짜로 정규화한다 (다른 페이지/탭이
  // stale 한 값을 읽지 않도록). setState 호출은 없으므로 cascading render
  // 가 발생하지 않는다.
  useEffect(() => {
    localStorage.setItem("plan_base_date", getKstToday().replace(/-/g, ""));
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
