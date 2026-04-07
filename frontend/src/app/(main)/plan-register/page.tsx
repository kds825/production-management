"use client";

import { useRef, useState, useCallback, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";
import { BatchSplitReview } from "@/features/plan-register/components/BatchSplitReview";

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
  file: File; // 실제 File 객체 — Stage 1 API에 전송용
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

interface SplitChunk {
  lot_index: number;
  order_count: number;
  total_m: number;
  min_due: string;
  max_due: string;
  order_ids: string[];
  batch_ids: number[];
}

interface SplitCandidate {
  batch_group: string;
  equipment_code: string | null;
  sq_mm2: number;
  lot_count: number;
  total_length_m: number;
  proposed_splits: SplitChunk[];
  gaps_days: number[];
  equipment_load_hours: number;
}

interface Stage1Result {
  run_label: string;
  parsed_orders: ParsedOrder[];
  batches: ParsedBatch[];
  warnings: string[];
  split_candidates?: SplitCandidate[];
  // incremental update 결과 요약 (stage1/update 응답에만 포함될 수 있음)
  added_orders?: number;
  created_batch_groups?: number;
  preserved_batches?: number;
}

type UploadMode = "full" | "incremental";

interface BatchStatusSummary {
  total_batches: number;
  completed: number;
  in_progress: number;
  planned: number;
  frozen_wip_count: number;
  available_wip_count: number;
}

function WipUploadSection({
  wipFile,
  setWipFile,
}: {
  wipFile: WipFile | null;
  setWipFile: (f: WipFile | null) => void;
}) {
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
      file,
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
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold" style={{ color: "#111827" }}>
          2. 재공수량 파일 업로드
        </h3>
        <a
          href={`${API}/pipeline/wip-template`}
          download="wip_template.xlsx"
          className="text-[11px] font-medium px-3 py-1 rounded-md transition-colors"
          style={{
            border: `1px solid ${PRIMARY}`,
            color: PRIMARY,
          }}
        >
          템플릿 다운로드
        </a>
      </div>
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
function ErpUploadSection({
  wipFile,
  onUploadModeChange,
}: {
  wipFile: WipFile | null;
  onUploadModeChange?: (mode: UploadMode) => void;
}) {
  const [erpFile, setErpFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [showDeleteHover, setShowDeleteHover] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<Stage1Result | null>(null);
  const [splitGapDays, setSplitGapDays] = useState(3);
  const [splitModalOpen, setSplitModalOpen] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  // 업로드 모드: "full" = 전체 교체, "incremental" = 긴급수주 추가
  const [uploadMode, setUploadMode] = useState<UploadMode>("full");
  // 확인 모달 (Stage 1 실행 전 현황 확인)
  const [confirmModalOpen, setConfirmModalOpen] = useState(false);
  const [batchSummary, setBatchSummary] = useState<BatchStatusSummary | null>(
    null,
  );
  // 성공 토스트 메시지
  const [successToast, setSuccessToast] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleUploadModeChange = useCallback(
    (mode: UploadMode) => {
      setUploadMode(mode);
      onUploadModeChange?.(mode);
    },
    [onUploadModeChange],
  );

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

  // 에러 텍스트를 사용자 친화적 메시지로 변환
  const parseApiError = useCallback(async (res: Response): Promise<string> => {
    const errText = await res.text();
    let userMsg = `서버 오류 (${res.status})`;
    try {
      const errJson = JSON.parse(errText);
      const detail = errJson.detail || "";
      // 첫 줄만 추출 (SQL 쿼리/파라미터 제거)
      userMsg =
        typeof detail === "string" ? detail.split("\n")[0] : String(detail);
      if (userMsg.length > 100) userMsg = userMsg.slice(0, 100) + "...";
    } catch {
      if (errText.length > 100) userMsg = errText.slice(0, 100) + "...";
    }
    return userMsg;
  }, []);

  // 실제 Stage 1 API 호출 (확인 모달 통과 후)
  const executeStage1 = useCallback(
    async (parentRunLabel?: string) => {
      if (!erpFile || isRunning) return;
      setConfirmModalOpen(false);
      setIsRunning(true);
      setApiError(null);
      setResult(null);
      setSuccessToast(null);

      try {
        const formData = new FormData();
        formData.append("erp_file", erpFile);
        if (wipFile?.file) {
          formData.append("wip_file", wipFile.file);
        }
        formData.append("split_gap_days", String(splitGapDays));

        let endpoint = `${API}/pipeline/stage1`;

        // 증분 모드이거나 기존 run이 있으면 update 엔드포인트 사용
        if (uploadMode === "incremental" || parentRunLabel) {
          endpoint = `${API}/pipeline/stage1/update`;
          formData.append("upload_mode", uploadMode);
          if (parentRunLabel) {
            formData.append("parent_run_label", parentRunLabel);
          }
        }

        const res = await fetch(endpoint, {
          method: "POST",
          body: formData,
        });

        if (!res.ok) {
          throw new Error(await parseApiError(res));
        }

        const data: Stage1Result = await res.json();
        setResult(data);

        // 업데이트 결과 요약 토스트 표시
        if (parentRunLabel && data.added_orders !== undefined) {
          const parts: string[] = [];
          if (data.added_orders) parts.push(`${data.added_orders}건 추가`);
          if (data.created_batch_groups)
            parts.push(`${data.created_batch_groups}개 배치그룹 생성`);
          if (data.preserved_batches)
            parts.push(`${data.preserved_batches}개 보존`);
          if (parts.length > 0) setSuccessToast(parts.join(", "));
        }
      } catch (err) {
        setApiError(
          err instanceof Error
            ? err.message
            : "알 수 없는 오류가 발생했습니다.",
        );
      } finally {
        setIsRunning(false);
      }
    },
    [erpFile, isRunning, splitGapDays, wipFile, uploadMode, parseApiError],
  );

  // Stage 1 실행 버튼 클릭 핸들러 — 기존 배치가 있으면 확인 모달 선표시
  const handleRunStage1 = useCallback(async () => {
    if (!erpFile || isRunning) return;

    // 1. 현재 배치 상태 조회
    let summary: BatchStatusSummary | null = null;
    try {
      const res = await fetch(`${API}/pipeline/batch-status-summary`);
      if (res.ok) {
        summary = await res.json();
      }
    } catch {
      // 상태 조회 실패 시 조용히 무시하고 직접 실행
    }

    // 2. 기존 배치가 있거나 증분 모드이면 확인 모달 표시
    if (
      summary &&
      (summary.total_batches > 0 || uploadMode === "incremental")
    ) {
      setBatchSummary(summary);
      setConfirmModalOpen(true);
      return;
    }

    // 3. 기존 배치 없음 + 전체 모드 → 레거시 /stage1 직접 실행
    await executeStage1();
  }, [erpFile, isRunning, executeStage1]);

  // 확인 모달에서 "업로드 진행" 클릭 시 — 최신 run_label을 받아 executeStage1 호출
  const handleConfirmUpload = useCallback(async () => {
    let parentRunLabel: string | undefined;
    try {
      const res = await fetch(`${API}/pipeline/runs`);
      if (res.ok) {
        const runs: Array<{ run_label: string }> = await res.json();
        if (runs.length > 0) parentRunLabel = runs[0].run_label;
      }
    } catch {
      // run_label 조회 실패 시 update 엔드포인트 없이 실행
    }
    await executeStage1(parentRunLabel);
  }, [executeStage1]);

  const uploadAreaBorderColor = isDragOver ? PRIMARY : "#D1D5DB";

  // 업로드 모드별 설명 텍스트
  const modeDescription =
    uploadMode === "full"
      ? "ERP 전체 파일로 기존 계획을 교체합니다. 진행중/완료 배치는 보존됩니다."
      : "긴급수주 파일의 주문만 기존 계획에 추가합니다.";

  return (
    <section className="mb-6">
      <h3 className="text-sm font-semibold mb-1" style={{ color: "#111827" }}>
        3. ERP 작업지시 파일 업로드 및 Stage 1 실행
      </h3>
      <p className="text-xs text-gray-500 mb-3">
        ERP에서 추출한 작업지시서 파일(.xls)을 업로드하고 작업지시서를
        생성하세요
      </p>

      {/* 업로드 모드 세그먼트 컨트롤 */}
      <div className="mb-3">
        <div
          className="inline-flex rounded-lg overflow-hidden"
          style={{ border: "1px solid #E5E7EB" }}
        >
          {(
            [
              { value: "full", label: "전체 교체" },
              { value: "incremental", label: "긴급수주 추가" },
            ] as const
          ).map(({ value, label }) => (
            <button
              key={value}
              onClick={() => handleUploadModeChange(value)}
              className="px-4 py-1.5 text-xs font-medium transition-colors"
              style={{
                backgroundColor: uploadMode === value ? PRIMARY : "#FFFFFF",
                color: uploadMode === value ? "#FFFFFF" : "#4B5563",
                borderRight: value === "full" ? "1px solid #E5E7EB" : undefined,
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-gray-500 mt-1.5">{modeDescription}</p>
      </div>

      {/* 성공 토스트 */}
      {successToast && (
        <div
          className="mb-3 rounded-lg px-3 py-2 text-xs flex items-center justify-between"
          style={{
            backgroundColor: "#F0FDF4",
            border: "1px solid #BBF7D0",
            color: "#166534",
          }}
        >
          <span>
            <span className="font-semibold">업데이트 완료: </span>
            {successToast}
          </span>
          <button
            onClick={() => setSuccessToast(null)}
            className="ml-3 text-green-400 hover:text-green-600 text-base leading-none"
          >
            ✕
          </button>
        </div>
      )}

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

          {/* Batch split review modal trigger */}
          {result.split_candidates && result.split_candidates.length > 0 && (
            <>
              <button
                onClick={() => setSplitModalOpen(true)}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium transition-colors"
                style={{
                  border: "1px solid #BFDBFE",
                  backgroundColor: "#F0F7FF",
                  color: "#1E40AF",
                }}
              >
                <span>✂️</span>
                배치 분할 검토
                <span
                  className="rounded-full px-1.5 py-0.5 text-[10px] font-bold"
                  style={{ backgroundColor: "#DBEAFE", color: "#1E40AF" }}
                >
                  {result.split_candidates.length}
                </span>
              </button>

              {/* Modal */}
              {splitModalOpen && (
                <div
                  className="fixed inset-0 z-50 flex items-center justify-center"
                  style={{ backgroundColor: "rgba(0,0,0,0.4)" }}
                  onClick={(e) => {
                    if (e.target === e.currentTarget) setSplitModalOpen(false);
                  }}
                >
                  <div
                    className="relative rounded-xl shadow-2xl max-w-4xl w-full max-h-[85vh] overflow-y-auto"
                    style={{ backgroundColor: "#FFFFFF" }}
                  >
                    <div
                      className="sticky top-0 z-10 flex items-center justify-between px-5 py-3 border-b"
                      style={{ backgroundColor: "#F8FAFC" }}
                    >
                      <span className="text-sm font-semibold text-gray-800">
                        ✂️ 배치 분할 검토
                      </span>
                      <button
                        onClick={() => setSplitModalOpen(false)}
                        className="text-gray-400 hover:text-gray-600 text-lg"
                      >
                        ✕
                      </button>
                    </div>
                    <div className="p-5">
                      <BatchSplitReview
                        candidates={result.split_candidates}
                        runLabel={result.run_label}
                        gapDays={splitGapDays}
                        onGapDaysChange={setSplitGapDays}
                        onApplied={() => {}}
                        onClose={() => setSplitModalOpen(false)}
                      />
                    </div>
                  </div>
                </div>
              )}
            </>
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
  const [baseDate, setBaseDate] = useState(getKstToday());
  const [wipFile, setWipFile] = useState<WipFile | null>(null);

  // localStorage에서 기존 기준일자 복원, 없으면 오늘로 초기화 후 저장
  useEffect(() => {
    const stored = localStorage.getItem("plan_base_date");
    if (stored && stored.length === 8) {
      const formatted = `${stored.slice(0, 4)}-${stored.slice(4, 6)}-${stored.slice(6, 8)}`;
      setBaseDate(formatted);
    } else {
      const today = getKstToday();
      localStorage.setItem("plan_base_date", today.replace(/-/g, ""));
    }
  }, []);

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
        {/* 계획 기준일자 */}
        <section className="mb-6">
          <h3
            className="text-sm font-semibold mb-1"
            style={{ color: "#111827" }}
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
              borderColor: "#D1D5DB",
              color: "#111827",
              outline: "none",
            }}
          />
        </section>

        <WipUploadSection wipFile={wipFile} setWipFile={setWipFile} />
        <ErpUploadSection wipFile={wipFile} />
      </div>
    </div>
  );
}
