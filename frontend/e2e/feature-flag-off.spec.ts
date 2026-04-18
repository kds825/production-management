/**
 * Task 27 — FEATURE_FLAG_CASCADE_V2 off 시 legacy flow 유지 확인.
 *
 * 검증 포인트:
 *   - 드래그 이동 시 /cascade-preview 엔드포인트가 호출되지 않는다.
 *   - legacy scheduleStore.moveTask 경로만 타서 모달 없이 즉시 반영된다.
 *
 * 주의 — 왜 조건부 skip 인가:
 *   FEATURE_FLAG_CASCADE_V2 는 NEXT_PUBLIC_* 빌드 타임 상수다. dev 서버가
 *   flag on 으로 뜨면 이 테스트는 의미가 없으므로 test.skip 로 명시 스킵한다.
 *   flag off 빌드를 별도로 기동해서 이 spec 만 돌릴 때 유효.
 */
import { test, expect } from "./fixtures/cascadeFixture";
import { FEATURE_FLAG_CASCADE_V2 } from "../src/shared/config/featureFlags";

test.describe("Feature flag off — legacy flow", () => {
  test.skip(
    FEATURE_FLAG_CASCADE_V2,
    "FEATURE_FLAG_CASCADE_V2 is on in this build — off-build required for this spec",
  );

  test("드래그 이동 시 cascade-preview 호출 없음 + 모달 없음", async ({
    page,
    frozenTime,
    seededSchedule,
  }) => {
    void frozenTime;
    void seededSchedule;

    const cascadeCalls: string[] = [];
    // request 이벤트는 navigate 이전에 리스너를 붙여야 초기 호출까지 잡힌다.
    page.on("request", (req) => {
      if (req.url().includes("/cascade-preview")) {
        cascadeCalls.push(req.url());
      }
    });

    await page.goto("/scheduler");
    await expect(page.getByTestId("gantt-block-A")).toBeVisible({
      timeout: 15_000,
    });

    const source = page.getByTestId("gantt-block-A");
    const target = page.getByTestId("gantt-block-B");
    await expect(target).toBeVisible();
    await source.dragTo(target);

    // legacy flow: 모달 없이 즉시 반영
    await expect(
      page.getByRole("dialog", { name: /배치 변경 확인/ }),
    ).not.toBeVisible();

    // cascade-preview 호출 0건
    expect(cascadeCalls).toEqual([]);
  });
});
