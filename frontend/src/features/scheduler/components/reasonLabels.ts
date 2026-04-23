/**
 * Cascade preview 의 push/pull/unresolved reason code → 한국어 라벨 매핑.
 *
 * - 백엔드 enum (`backend/app/presentation/schemas/cascade.py`) 의 각 code 와 1:1 매핑.
 * - 모달에서 사용자에게 "왜 이 블록이 밀리는가"를 설명하는 유일한 카피 소스.
 * - 알 수 없는 코드는 원문을 그대로 노출해서 디버깅 힌트를 남긴다 (silent drop 금지).
 */
import type {
  PushReasonCode,
  PullReasonCode,
  UnresolvedReasonCode,
} from "../api/cascade.types";

export const PUSH_REASON_LABEL: Record<PushReasonCode, string> = {
  same_equipment_conflict: "같은 설비의 다음 블록과 충돌로 밀림",
  cross_equipment_conflict: "후속 공정 설비의 다른 블록과 충돌로 밀림",
  successor_chain: "선행 공정 지연으로 시작 시간 밀림",
};

export const PULL_REASON_LABEL: Record<PullReasonCode, string> = {
  successor_slack_available: "선행 공정 단축으로 앞당김 가능",
};

export const UNRESOLVED_REASON_LABEL: Record<UnresolvedReasonCode, string> = {
  due_date_violation: "납기 초과 — 재배치 불가",
  no_space_forward: "해당 설비에 공간 없음",
  cycle_detected: "연쇄가 너무 복잡 — 수동 조정 필요",
  invalid_equipment: "해당 설비는 이 공정을 수행할 수 없음",
};

/**
 * push/pull 행에서 reason code → 한국어 라벨.
 * 세 dictionary 어디에도 없는 코드는 원문을 반환 — 프론트에서 silent drop 금지.
 */
export function labelForReason(code: string): string {
  return (
    (PUSH_REASON_LABEL as Record<string, string>)[code] ??
    (PULL_REASON_LABEL as Record<string, string>)[code] ??
    (UNRESOLVED_REASON_LABEL as Record<string, string>)[code] ??
    code
  );
}
