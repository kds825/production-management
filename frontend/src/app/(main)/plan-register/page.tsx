"use client";

import { useEffect, useState } from "react";
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
  // baseDate 초기값: 항상 오늘.
  // - 이 페이지는 "새로운 생산계획 수립" 의 진입점이므로 mount 시점마다
  //   today 로 리셋하는 것이 사용자 멘탈 모델과 일치한다.
  // - localStorage 의 plan_base_date 는 cross-page 채널 (scheduling-review,
  //   useScheduleData, useAutoSchedule 가 읽음) 이므로 mount 시점에 today
  //   로 동기화해 이전 세션의 stale 값이 다른 페이지로 새지 않게 한다.
  // - 사용자가 input 으로 변경하면 onChange 가 state + localStorage 둘 다
  //   업데이트 → 같은 세션의 후속 flow (Stage1/Stage2/간트) 에 그 값이 흐른다.
  const [baseDate, setBaseDate] = useState<string>(getKstToday);
  const [wipFile, setWipFile] = useState<WipFile | null>(null);
  // incremental 모드 선택 시 WIP 섹션을 흐리게 처리하기 위해 모드를 상위에서 관리
  const [erpUploadMode, setErpUploadMode] = useState<UploadMode>("full");

  // Mount 시점에 localStorage 를 today 로 동기화.
  // 이전 세션에서 4/1 등을 저장한 채 남아 있을 수 있는데, 새 plan-register
  // 진입은 새 계획 수립 의미이므로 stale 값이 cross-page 채널을 통해 다른
  // 페이지로 흘러가지 않도록 명시적으로 덮어쓴다.
  useEffect(() => {
    if (typeof window !== "undefined") {
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
