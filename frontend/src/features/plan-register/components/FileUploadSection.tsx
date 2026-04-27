"use client";

import { useRef, useState, useCallback, useEffect } from "react";
import { usePlanRegisterStore } from "../store/planRegisterStore";

const ACCEPTED_EXTENSIONS = [".xls", ".xlsx"];
const PRIMARY = "var(--color-brand-primary)";

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

export function FileUploadSection() {
  const {
    uploadedFile,
    isAnalyzing,
    isAnalyzed,
    setUploadedFile,
    setIsAnalyzing,
    setIsAnalyzed,
    setBatches,
  } = usePlanRegisterStore();

  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [showDeleteHover, setShowDeleteHover] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const analyzeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (analyzeTimerRef.current) clearTimeout(analyzeTimerRef.current);
    };
  }, []);

  const handleFile = useCallback(
    (file: File) => {
      setValidationError(null);
      if (!isValidExtension(file.name)) {
        setValidationError(".xls 또는 .xlsx 파일만 업로드 가능합니다.");
        return;
      }
      setUploadedFile({
        name: file.name,
        size: file.size,
        uploadedAt: new Date(),
      });
      setIsAnalyzed(false);
      setBatches([]);
    },
    [setUploadedFile, setIsAnalyzed, setBatches],
  );

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
      // reset input so same file can be re-selected
      e.target.value = "";
    },
    [handleFile],
  );

  const handleAnalyze = useCallback(() => {
    if (!uploadedFile || isAnalyzing) return;
    setIsAnalyzing(true);

    // Stage 1 API 결과 기반 — 분석은 ErpUploadSection에서 처리
    analyzeTimerRef.current = setTimeout(() => {
      analyzeTimerRef.current = null;
      setIsAnalyzing(false);
      setIsAnalyzed(true);
    }, 1500);
  }, [uploadedFile, isAnalyzing, setIsAnalyzing, setBatches, setIsAnalyzed]);

  const handleDelete = useCallback(() => {
    if (analyzeTimerRef.current) {
      clearTimeout(analyzeTimerRef.current);
      analyzeTimerRef.current = null;
    }
    setUploadedFile(null);
    setBatches([]);
    setIsAnalyzing(false);
    setIsAnalyzed(false);
    setValidationError(null);
  }, [setUploadedFile, setBatches, setIsAnalyzing, setIsAnalyzed]);

  const uploadAreaBorderColor = isDragOver ? PRIMARY : "var(--neutral-300)";

  return (
    <section className="mb-6">
      <h3
        className="text-sm font-semibold mb-1"
        style={{ color: "var(--color-text-primary)" }}
      >
        1. 생산계획등록 파일 업로드
      </h3>
      <p className="text-xs text-gray-500 mb-3">
        진행상태가 '진행' 또는 '대기'인 파일을 업로드해주세요
      </p>

      {/* 업로드 영역 */}
      {!uploadedFile ? (
        <div
          className="rounded-lg flex flex-col items-center justify-center gap-2 cursor-pointer transition-colors"
          style={{
            border: `1.5px dashed ${uploadAreaBorderColor}`,
            backgroundColor: isDragOver
              ? "var(--kbi-red-tint-5)"
              : "var(--bg-surface)",
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
            stroke={isDragOver ? PRIMARY : "var(--color-text-tertiary)"}
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
          <p className="text-tiny text-gray-400">.xls, .xlsx 파일 지원</p>
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
        /* 파일 카드 */
        <div
          className="rounded-lg p-3 flex items-center justify-between gap-3"
          style={{
            border: "1px solid var(--color-border-default)",
            backgroundColor: "var(--bg-surface)",
          }}
          onMouseEnter={() => setShowDeleteHover(true)}
          onMouseLeave={() => setShowDeleteHover(false)}
        >
          <div className="flex items-center gap-3 min-w-0">
            <div
              className="flex-shrink-0 rounded flex items-center justify-center"
              style={{
                width: 36,
                height: 36,
                backgroundColor: "var(--kbi-red-tint-5)",
              }}
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
                style={{ color: "var(--color-text-primary)" }}
              >
                {uploadedFile.name}
              </p>
              <p className="text-tiny text-gray-400 mt-0.5">
                {formatFileSize(uploadedFile.size)} &middot;{" "}
                {formatTime(uploadedFile.uploadedAt)} 업로드
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {showDeleteHover && !isAnalyzing && (
              <button
                onClick={handleDelete}
                className="text-small font-medium px-2.5 py-1.5 rounded-md transition-colors"
                style={{
                  border: "1px solid var(--color-border-default)",
                  color: "var(--color-text-secondary)",
                  backgroundColor: "transparent",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor =
                    "var(--color-brand-primary)";
                  e.currentTarget.style.color = "var(--color-brand-primary)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor =
                    "var(--color-border-default)";
                  e.currentTarget.style.color = "var(--color-text-secondary)";
                }}
              >
                삭제하기
              </button>
            )}
            <button
              onClick={handleAnalyze}
              disabled={isAnalyzing || isAnalyzed}
              className="text-small font-medium px-3 py-1.5 rounded-md flex items-center gap-1.5 transition-colors"
              style={{
                backgroundColor:
                  isAnalyzing || isAnalyzed
                    ? "var(--color-border-default)"
                    : PRIMARY,
                color:
                  isAnalyzing || isAnalyzed
                    ? "var(--color-text-tertiary)"
                    : "var(--color-text-inverse)",
                cursor: isAnalyzing || isAnalyzed ? "not-allowed" : "pointer",
              }}
            >
              {isAnalyzing ? (
                <>
                  <svg
                    className="animate-spin"
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                  >
                    <path
                      d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"
                      opacity="0.25"
                    />
                    <path d="M21 12a9 9 0 0 0-9-9" />
                  </svg>
                  분석 중...
                </>
              ) : isAnalyzed ? (
                "분석 완료"
              ) : (
                "분석하기"
              )}
            </button>
          </div>
        </div>
      )}

      {/* 유효성 오류 메시지 */}
      {validationError && (
        <p
          className="text-small mt-1.5"
          style={{ color: "var(--color-brand-primary)" }}
        >
          {validationError}
        </p>
      )}
    </section>
  );
}
