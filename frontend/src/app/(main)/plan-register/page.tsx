"use client";

import Image from "next/image";
import { FileUploadSection } from "@/features/plan-register/components/FileUploadSection";
import { ScheduleReviewTable } from "@/features/plan-register/components/ScheduleReviewTable";

export default function PlanRegisterPage() {
  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
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
            style={{ color: "#4A2C2A", letterSpacing: "-0.02em" }}
          >
            생산계획등록
          </h1>
        </div>
      </header>

      {/* 본문 */}
      <div className="flex-1 overflow-auto px-6 py-6 flex flex-col gap-0">
        <div className="mb-8">
          <FileUploadSection />
        </div>
        <ScheduleReviewTable />
      </div>
    </div>
  );
}
