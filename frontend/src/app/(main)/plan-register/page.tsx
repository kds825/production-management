"use client";

import { useRef, useState, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";
import { FileUploadSection } from "@/features/plan-register/components/FileUploadSection";

const ACCEPTED_EXTENSIONS = [".xls", ".xlsx"];
const PRIMARY = "#C41230";
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

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

// Stage 1 API response types
interface ParsedOrder {
  order_id: string;
  product_code: string;
  quantity: number;
  due_date: string;
}

interface ParsedBatch {
  batch_id: number;
  process: string;
  order_count: number;
  total_quantity: number;
}

interface Stage1Result {
  run_label: string;
  parsed_orders: ParsedOrder[];
  batches: ParsedBatch[];
  warnings: string[];
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

// ERP 업로드 + Stage 1 트리거 섹션
function ErpUploadSection() {
  const [erpFile, setErpFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [showDeleteHover, setShowDeleteHover] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<Stage1Result | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((file: File) => {
    setValidationError(null);
    setResult(null);
    setApiError(null);
    if (!isValidExtension(file.name)) {
      setValidationError(".xls 또는 .xlsx 파일만 업로드 가능합니다.");
      return;
    }
    setErpFile(file);
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
    setErpFile(null);
    setResult(null);
    setApiError(null);
    setValidationError(null);
  }, []);

  // POST to /api/pipeline/stage1 with the ERP file
  const handleRunStage1 = useCallback(async () => {
    if (!erpFile || isRunning) return;
    setIsRunning(true);
    setApiError(null);
    setResult(null);

    try {
      const formData = new FormData();
      formData.append("file", erpFile);

      const res = await fetch(`${API}/pipeline/stage1`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`서버 오류 (${res.status}): ${errText}`);
      }

      const data: Stage1Result = await res.json();
      setResult(data);
    } catch (err) {
      setApiError(
        err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다.",
      );
    } finally {
      setIsRunning(false);
    }
  }, [erpFile, isRunning]);

  const uploadAreaBorderColor = isDragOver ? PRIMARY : "#D1D5DB";

  return (
    <section className="mb-6">
      <h3 className="text-sm font-semibold mb-1" style={{ color: "#111827" }}>
        3. ERP 작업지시 파일 업로드 및 Stage 1 실행
      </h3>
      <p className="text-xs text-gray-500 mb-3">
        ERP에서 추출한 작업지시서 파일(.xls)을 업로드하고 작업지시서를
        생성하세요
      </p>

      {/* File upload area */}
      {!erpFile ? (
        <div
          className="rounded-lg flex flex-col items-center justify-center gap-2 cursor-pointer"
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
            className="mt-2 px-4 py-1.5 text-xs font-medium rounded-md text-white"
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
                {erpFile.name}
              </p>
              <p className="text-[10px] text-gray-400 mt-0.5">
                {formatFileSize(erpFile.size)}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {showDeleteHover && !isRunning && !result && (
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
            {/* "작업지시서 생성" trigger button */}
            <button
              onClick={handleRunStage1}
              disabled={isRunning || !!result}
              className="text-[11px] font-medium px-3 py-1.5 rounded-md flex items-center gap-1.5 transition-colors"
              style={{
                backgroundColor: isRunning || result ? "#E5E7EB" : PRIMARY,
                color: isRunning || result ? "#9CA3AF" : "#FFFFFF",
                cursor: isRunning || result ? "not-allowed" : "pointer",
              }}
            >
              {isRunning ? (
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
                  처리 중...
                </>
              ) : result ? (
                "생성 완료"
              ) : (
                "작업지시서 생성"
              )}
            </button>
          </div>
        </div>
      )}

      {validationError && (
        <p className="text-[11px] mt-1.5" style={{ color: "#C41230" }}>
          {validationError}
        </p>
      )}

      {/* API error */}
      {apiError && (
        <div
          className="mt-3 rounded-lg p-3 text-xs"
          style={{
            backgroundColor: "#FEF2F2",
            border: "1px solid #FECACA",
            color: "#991B1B",
          }}
        >
          <span className="font-semibold">오류: </span>
          {apiError}
        </div>
      )}

      {/* Stage 1 result */}
      {result && (
        <div className="mt-4 space-y-3">
          {/* Warnings */}
          {result.warnings && result.warnings.length > 0 && (
            <div
              className="rounded-lg p-3 text-xs space-y-1"
              style={{
                backgroundColor: "#FFFBEB",
                border: "1px solid #FDE68A",
              }}
            >
              <p className="font-semibold text-yellow-800">경고</p>
              {result.warnings.map((w, i) => (
                <p key={i} className="text-yellow-700">
                  {w}
                </p>
              ))}
            </div>
          )}

          {/* Batch summary */}
          {result.batches && result.batches.length > 0 && (
            <div
              className="rounded-lg p-3"
              style={{
                border: "1px solid #E5E7EB",
                backgroundColor: "#FFFFFF",
              }}
            >
              <p
                className="text-xs font-semibold mb-2"
                style={{ color: "#111827" }}
              >
                생성된 배치 ({result.batches.length}개)
              </p>
              <div className="space-y-1">
                {result.batches.map((batch) => (
                  <div
                    key={batch.batch_id}
                    className="flex items-center gap-3 text-xs py-1 border-b last:border-b-0"
                    style={{ borderColor: "#F3F4F6" }}
                  >
                    <span
                      className="rounded px-1.5 py-0.5 font-medium"
                      style={{ backgroundColor: "#FEF2F2", color: PRIMARY }}
                    >
                      배치#{batch.batch_id}
                    </span>
                    <span className="font-medium text-gray-700">
                      {batch.process}
                    </span>
                    <span className="text-gray-400">
                      주문 {batch.order_count}건
                    </span>
                    <span className="text-gray-400">
                      총 {batch.total_quantity.toLocaleString()} m
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Parsed orders summary */}
          {result.parsed_orders && result.parsed_orders.length > 0 && (
            <div
              className="rounded-lg p-3"
              style={{
                border: "1px solid #E5E7EB",
                backgroundColor: "#FFFFFF",
              }}
            >
              <p
                className="text-xs font-semibold mb-2"
                style={{ color: "#111827" }}
              >
                파싱된 주문 ({result.parsed_orders.length}건)
              </p>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {result.parsed_orders.map((order) => (
                  <div
                    key={order.order_id}
                    className="flex items-center gap-3 text-xs py-1 border-b last:border-b-0"
                    style={{ borderColor: "#F3F4F6" }}
                  >
                    <span className="font-mono text-gray-500 shrink-0">
                      {order.order_id}
                    </span>
                    <span className="font-medium text-gray-700 truncate">
                      {order.product_code}
                    </span>
                    <span className="text-gray-400 shrink-0">
                      {order.quantity.toLocaleString()} m
                    </span>
                    <span className="text-gray-400 shrink-0">
                      {order.due_date}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Link to scheduling-review */}
          <div className="flex justify-end">
            <Link
              href="/scheduling-review"
              className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md text-white transition-opacity hover:opacity-80"
              style={{ backgroundColor: PRIMARY }}
            >
              스케줄링 검토 페이지에서 결과 확인
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="5" y1="12" x2="19" y2="12" />
                <polyline points="12 5 19 12 12 19" />
              </svg>
            </Link>
          </div>
        </div>
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
        <ErpUploadSection />
      </div>
    </div>
  );
}
