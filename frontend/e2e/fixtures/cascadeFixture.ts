/**
 * Cascade E2E 공용 fixture (Task 24).
 *
 * 왜 fixture 로 분리했나:
 *   Tasks 25–27 의 모든 cascade 시나리오가 "고정 시각 + 재현 가능한 seed" 라는
 *   동일 전제 위에 돌아간다. 각 spec 이 Date.now/seed 설정을 복붙하면 유지보수
 *   비용이 커지므로, Playwright fixture 로 한 군데에 모아둔다.
 *
 * 두 fixture:
 *   - frozenTime:       브라우저 컨텍스트의 Date.now() 를 2026-04-18 00:00Z 로 고정.
 *                       스케줄 데이터 내 "오늘" 의존 로직이 세션마다 달라지는 것을 방지.
 *                       initScript 로 주입하므로 페이지 navigate 이전에 적용됨.
 *   - seededSchedule:   백엔드에 테스트 전용 seed reset 엔드포인트(/api/test/reset-schedule)
 *                       가 있을 때만 호출. 없거나 실패하면 test.skip 로 명시적 스킵.
 *                       seed 가 없는 환경에서는 수동 seed 가 전제이며, seed fixture 이름
 *                       ("cascade-e2e-base") 을 return 하여 테스트 로직이 어떤 seed 를
 *                       가정했는지 추적 가능하게 한다.
 *
 * 주의:
 *   - Date.now override 는 Date 생성자/Date.prototype 은 건드리지 않는다. 대부분의
 *     애플리케이션 코드가 Date.now() 또는 new Date() 로 현재 시각을 읽는데, new Date()
 *     의 no-arg 생성자는 내부적으로 시스템 시계를 쓰므로 완벽한 시간 정지는 아니다.
 *     cascade 시나리오는 초단위 정밀도가 필요 없으므로 충분하다.
 */
import { test as base, expect } from "@playwright/test";

type Fixtures = {
  frozenTime: string;
  seededSchedule: string;
};

const FROZEN_ISO = "2026-04-18T00:00:00.000Z";
const SEED_ENDPOINT = "/api/test/reset-schedule";
const SEED_FIXTURE_NAME = "cascade-e2e-base";

export const test = base.extend<Fixtures>({
  frozenTime: async ({ page }, use) => {
    // addInitScript: navigate 직전에 새 프레임마다 실행됨.
    // Date.now() 만 override — 호환성 문제 최소화.
    await page.addInitScript((iso: string) => {
      const FROZEN = new Date(iso).getTime();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (Date as any).now = () => FROZEN;
    }, FROZEN_ISO);
    await use(FROZEN_ISO);
  },

  seededSchedule: async ({ request }, use) => {
    // 백엔드 seed reset 엔드포인트 존재 여부를 단순 POST 로 확인한다.
    // 404/5xx 면 endpoint 없음/실패 → test.skip 으로 명시.
    let available = false;
    try {
      const res = await request.post(SEED_ENDPOINT, {
        data: { fixture: SEED_FIXTURE_NAME },
      });
      available = res.ok();
      if (!available) {
        // skip 은 테스트 레벨에서 유효. fixture setup 단계에서 test.skip 을 부르면
        // 해당 테스트만 skip 되고 나머지는 계속 돈다.
        test.skip(
          true,
          `seed endpoint ${SEED_ENDPOINT} returned ${res.status()} — cascade E2E requires deterministic seed`,
        );
      }
    } catch (err) {
      test.skip(
        true,
        `seed endpoint ${SEED_ENDPOINT} unavailable (${(err as Error).message}) — manual seed required`,
      );
    }
    await use(SEED_FIXTURE_NAME);
  },
});

export { expect };
