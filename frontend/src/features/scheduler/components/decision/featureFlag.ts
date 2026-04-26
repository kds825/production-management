/**
 * decision_card_v2 feature flag — Step 4-MVP canary.
 *
 * 정책:
 *   - default OFF (안정화 1주차 운영자 1명 canary 후 전체 ON)
 *   - 환경변수 `NEXT_PUBLIC_DECISION_CARD_V2`: 'on' | 'off'
 *   - URL 쿼리 `?v2=1`: dev/canary 강제 ON (운영자별 개인 토글)
 *   - URL 쿼리 `?v2=0`: 환경변수 ON 이어도 강제 OFF (긴급 롤백 channel)
 *
 * 환경변수 OFF + URL 미지정 = v1 유지. 운영자에게는 변화 없음.
 */

export function isDecisionCardV2Enabled(): boolean {
  const envFlag = process.env.NEXT_PUBLIC_DECISION_CARD_V2;
  const envOn = envFlag === "on" || envFlag === "1" || envFlag === "true";

  if (typeof window === "undefined") return envOn;

  const params = new URLSearchParams(window.location.search);
  const queryV2 = params.get("v2");
  if (queryV2 === "1" || queryV2 === "on") return true;
  if (queryV2 === "0" || queryV2 === "off") return false;

  return envOn;
}
