"use client";

import { useState } from "react";

/**
 * URL 쿼리스트링에서 ?run_label= 값을 1회 추출.
 *
 * Next 16 의 next/navigation `useSearchParams` 는 SSR Suspense boundary 를
 * 요구해 build 가 실패한다. scheduling-review 는 client-only 페이지이므로
 * 마운트 직후 1회만 window.location 을 직접 읽는 편이 단순하고 안전하다.
 *
 * 구현 노트 (set-state-in-effect 회피):
 *  - 원본 page.tsx 는 useEffect 안에서 setState 를 호출했는데, hook 으로
 *    분리하면 React 19 의 `react-hooks/set-state-in-effect` 룰에 걸린다.
 *  - useState 의 lazy-initializer 안에서 window 를 읽으면 SSR 단계에서는
 *    typeof window === "undefined" 가 true → null 을 반환하고, 클라이언트
 *    초기 렌더에서 실제 값을 한 번에 결정하므로 cascading render 가 없다.
 *  - 외부 시그니처(string | null) 와 깊은-링크 우선순위 동작은 그대로.
 *
 * 운영자가 외부에서 깊은-링크로 run_label 을 전달했을 때 페이지가 임의의
 * 최신 run 으로 폴백하지 않도록, useRunsList 의 우선순위 결정에 사용한다.
 */
export function useUrlRunLabel(): string | null {
  const [urlRunLabel] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const params = new URLSearchParams(window.location.search);
    return params.get("run_label");
  });
  return urlRunLabel;
}
