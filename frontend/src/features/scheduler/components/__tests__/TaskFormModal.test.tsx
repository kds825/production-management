/**
 * Task 20 — TaskFormModal 의 duration-editable / 3-field 규칙 / 검증 / 토큰 치환 계약.
 *
 * 프로젝트 테스트 환경 제약:
 * - vitest `environment: "node"` 라 JSDOM 이 없고 RTL 도 미도입.
 * - zustand SSR 스냅샷은 `getInitialState()` 를 반환 → renderToStaticMarkup
 *   경로에서는 modal 이 항상 닫힌 상태로 렌더되어 DOM 단 검증이 불가능하다.
 *
 * 따라서 본 파일은 TaskFormModal 에서 export 된 **순수 헬퍼** 를 직접 호출해
 * 3-field 규칙의 핵심 계약을 단위 검증하고, 파일 내 raw hex 부재는 grep 으로
 * 별도 확인한다 (Step 6 verify 블록). DOM 레벨 상호작용은 Playwright E2E 담당.
 */
import { describe, it, expect } from "vitest";
import {
  validateStartEnd,
  computeEndFromDuration,
  toIsoNaive,
  calcHours,
} from "../TaskFormModal";

describe("TaskFormModal — Task 20: validateStartEnd (fail-fast)", () => {
  it("유효 구간(09:00-12:00) 은 null 을 반환한다", () => {
    expect(validateStartEnd("2026-04-20T09:00", "2026-04-20T12:00")).toBeNull();
  });

  it("start 또는 end 가 비어 있으면 '필수' 메시지", () => {
    expect(validateStartEnd("", "2026-04-20T12:00")).toBe(
      "시작/종료 시간 필수",
    );
    expect(validateStartEnd("2026-04-20T09:00", "")).toBe(
      "시작/종료 시간 필수",
    );
  });

  it("잘못된 포맷은 '잘못된 날짜 형식' 메시지", () => {
    expect(validateStartEnd("not-a-date", "2026-04-20T12:00")).toBe(
      "잘못된 날짜 형식",
    );
  });

  it("end <= start 이면 '이후여야 합니다' 메시지", () => {
    expect(validateStartEnd("2026-04-20T12:00", "2026-04-20T09:00")).toBe(
      "종료일시는 시작일시 이후여야 합니다",
    );
    // 동일 시각도 거부 (duration=0)
    expect(validateStartEnd("2026-04-20T09:00", "2026-04-20T09:00")).toBe(
      "종료일시는 시작일시 이후여야 합니다",
    );
  });
});

describe("TaskFormModal — Task 20: computeEndFromDuration (3-field rule)", () => {
  it("start 고정 + 3.0h → end = start + 3h", () => {
    expect(computeEndFromDuration("2026-04-20T09:00", 3)).toBe(
      "2026-04-20T12:00",
    );
  });

  it("0.5h 소수점 증분 지원", () => {
    expect(computeEndFromDuration("2026-04-20T09:00", 0.5)).toBe(
      "2026-04-20T09:30",
    );
  });

  it("h <= 0 또는 NaN 이면 null (저장 거부)", () => {
    expect(computeEndFromDuration("2026-04-20T09:00", 0)).toBeNull();
    expect(computeEndFromDuration("2026-04-20T09:00", -1)).toBeNull();
    expect(computeEndFromDuration("2026-04-20T09:00", NaN)).toBeNull();
  });

  it("start 가 빈 문자열이면 null", () => {
    expect(computeEndFromDuration("", 3)).toBeNull();
  });

  it("start 포맷 무효 시 null", () => {
    expect(computeEndFromDuration("not-a-date", 3)).toBeNull();
  });
});

describe("TaskFormModal — Task 20: toIsoNaive (백엔드 TZ-guard 계약)", () => {
  it("datetime-local (YYYY-MM-DDTHH:mm) 에 ':00' 초를 부가", () => {
    expect(toIsoNaive("2026-04-20T09:00")).toBe("2026-04-20T09:00:00");
  });

  it("이미 초까지 있는 ISO 는 그대로 반환", () => {
    expect(toIsoNaive("2026-04-20T09:00:00")).toBe("2026-04-20T09:00:00");
  });
});

describe("TaskFormModal — Task 20: calcHours (duration 표시값)", () => {
  it("09:00-12:00 = 3h", () => {
    const s = new Date("2026-04-20T09:00:00");
    const e = new Date("2026-04-20T12:00:00");
    expect(calcHours(s, e)).toBe(3);
  });

  it("end <= start 는 clamp 되어 0 을 반환 (음수 방지)", () => {
    const s = new Date("2026-04-20T12:00:00");
    const e = new Date("2026-04-20T09:00:00");
    expect(calcHours(s, e)).toBe(0);
  });
});
