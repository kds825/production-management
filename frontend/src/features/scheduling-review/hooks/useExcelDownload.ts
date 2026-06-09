"use client";

import { useCallback, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/**
 * 선택된 run 의 stage1 export 엑셀 파일을 다운로드하는 훅.
 *
 * Content-Disposition 헤더의 filename 을 우선 사용하고, 누락 시
 * `schedule_<run_label>.xlsx` 로 폴백한다. 실패 시 alert 로 사용자에게 노출.
 *
 * 외부 시그니처는 page.tsx 가 사용하던 excelLoading/handleExcelDownload 그대로.
 */
export function useExcelDownload(selectedRun: string) {
  const [excelLoading, setExcelLoading] = useState(false);

  const handleExcelDownload = useCallback(async () => {
    if (!selectedRun) return;
    setExcelLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/pipeline/stage1/${encodeURIComponent(selectedRun)}/export`,
      );
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        // Content-Disposition 헤더에서 파일명을 추출하거나 기본값 사용
        const disposition = res.headers.get("content-disposition");
        const match = disposition?.match(
          /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/,
        );
        a.download =
          match?.[1]?.replace(/['"]/g, "") ?? `schedule_${selectedRun}.xlsx`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        const body = await res
          .json()
          .catch(() => ({ detail: `HTTP ${res.status}` }));
        alert(`Excel 다운로드 실패: ${body.detail ?? res.statusText}`);
      }
    } catch (err) {
      alert(
        `Excel 다운로드 오류: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setExcelLoading(false);
    }
  }, [selectedRun]);

  return { excelLoading, handleExcelDownload };
}
