"use client";

import { useRef, useState, useCallback } from "react";
import Image from "next/image";
import { FileUploadSection } from "@/features/plan-register/components/FileUploadSection";

const ACCEPTED_EXTENSIONS = [".xls", ".xlsx"];
const PRIMARY = "#C41230";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function isValidExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

interface WipFile {
  name: string;
  size: number;
  uploadedAt: Date;
}

function WipUploadSection() {
  const [wipFile, setWipFile] = useState<WipFile | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [showDeleteHover, setShowDeleteHover] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((file: File) => {
    setValidationError(null);
    if (!isValidExtension(file.name)) {
      setValidationError(".xls 또는 .xlsx 파일만 업로드 가능합니다.");
      return;
    }
    setWipFile({
      name: file.name,
      size: file.size,
      uploadedAt: new Date(),
    });
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
      e.target.value = "";
    },
    [handleFile],
  );

  const handleDelete = useCallback(() => {
    setWipFile(null);
    setValidationError(null);
  }, []);

  const uploadAreaBorderColor = isDragOver ? PRIMARY : "#D1D5DB";

  return (
    <section className="mb-6">
      <h3 className="text-sm font-semibold mb-1" style={{ color: "#111827" }}>
        2. 재공수량 파일 업로드
      </h3>
      <p className="text-xs text-gray-500 mb-3">
        재공(WIP) 수량 데이터를 업로드해주세요
      </p>

      {!wipFile ? (
        <div
          className="rounded-lg flex flex-col items-center justify-center gap-2 cursor-pointer transition-colors"
          style={{
            border: `1.5px dashed ${uploadAreaBorderColor}`,
            backgroundColor: isDragOver ? "#FEF2F2" : "#FFFFFF",
            minHeight: 120,
            transition: "border-color 150ms ease, background-color 150ms ease",
          }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
        >
          <svg
            width="28"
            height="28"
            viewBox="0 0 24 24"
            fill="none"
            stroke={isDragOver ? PRIMARY : "#9CA3AF"}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          <p className="text-xs text-gray-500">
            클릭하거나 파일을 끌어다 놓으세요
          </p>
          <p className="text-[10px] text-gray-400">.xls, .xlsx 파일 지원</p>
          <button
            onClick={(e) => {
              e.stopPropagation();
              inputRef.current?.click();
            }}
            className="mt-2 px-4 py-1.5 text-xs font-medium rounded-md text-white transition-colors"
            style={{ backgroundColor: PRIMARY }}
          >
            파일 선택
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".xls,.xlsx"
            className="hidden"
            onChange={handleInputChange}
          />
        </div>
      ) : (
        <div
          className="rounded-lg p-3 flex items-center justify-between gap-3"
          style={{ border: "1px solid #E5E7EB", backgroundColor: "#FFFFFF" }}
          onMouseEnter={() => setShowDeleteHover(true)}
          onMouseLeave={() => setShowDeleteHover(false)}
        >
          <div className="flex items-center gap-3 min-w-0">
            <div
              className="flex-shrink-0 rounded flex items-center justify-center"
              style={{ width: 36, height: 36, backgroundColor: "#FEF2F2" }}
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke={PRIMARY}
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
            </div>
            <div className="min-w-0">
              <p
                className="text-xs font-medium truncate"
                style={{ color: "#111827" }}
              >
                {wipFile.name}
              </p>
              <p className="text-[10px] text-gray-400 mt-0.5">
                {formatFileSize(wipFile.size)} &middot;{" "}
                {formatTime(wipFile.uploadedAt)} 업로드
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {showDeleteHover && (
              <button
                onClick={handleDelete}
                className="text-[11px] font-medium px-2.5 py-1.5 rounded-md transition-colors"
                style={{
                  border: "1px solid #E5E7EB",
                  color: "#6B7280",
                  backgroundColor: "transparent",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = "#C41230";
                  e.currentTarget.style.color = "#C41230";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "#E5E7EB";
                  e.currentTarget.style.color = "#6B7280";
                }}
              >
                삭제하기
              </button>
            )}
            <span
              className="text-[11px] font-medium px-3 py-1.5 rounded-md"
              style={{ backgroundColor: "#DCFCE7", color: "#16A34A" }}
            >
              업로드 완료
            </span>
          </div>
        </div>
      )}

      {validationError && (
        <p className="text-[11px] mt-1.5" style={{ color: "#C41230" }}>
          {validationError}
        </p>
      )}
    </section>
  );
}

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
        <WipUploadSection />
      </div>
    </div>
  );
}
