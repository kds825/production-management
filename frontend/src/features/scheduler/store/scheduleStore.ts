/**
 * scheduleStore — 4 개 슬라이스(Orders / Batches / Diff / Filters)를 합성하는 thin composer.
 *
 * ── 간트 드래그/캐스케이드 동작 문서 (Task #7 조사) ──────────────────────
 *
 * 1. 대체설비 블록 이동:
 *    - equipmentMatchesGroup() (SchedulerView.tsx)가 드래그 대상 블록의 설비 그룹을 판별한다.
 *    - 허용 그룹: "연선", "B100", "A100", "A120"
 *    - 같은 그룹 내 설비 간에만 이동이 가능하고, 다른 그룹(예: 연선→B100)으로는 드롭이 차단된다.
 *
 * 2. Cascade Push (겹침 방지):
 *    - cascadePush() 는 같은 설비(equipment_id) 내에서만 작동한다.
 *    - 이동된 블록 기준 오른쪽 겹침 → forward push, 왼쪽 겹침 → backward push.
 *    - 설비 간(cross-equipment) cascade 는 현재 미구현.
 *
 * 3. 선행→후행 자동 연동 (moveTask 내부):
 *    - 같은 order_id 의 후속 공정(process_step 이 큰 작업)을 timeDelta 만큼 이동.
 *    - 후공정 시작은 선행 공정 종료 이후로 보장 (Math.max 적용).
 *    - 이동 후 후공정 설비 내에서 cascadePush 를 재적용.
 *    - 제한사항: cross-equipment 간 cascade 는 미구현이므로, 연선 블록을 이동해도
 *      절연/시스 설비의 다른 배치에 대한 연쇄 밀기는 발생하지 않는다.
 *
 * ── 슬라이스 구성 (Week 7 Task 7B.3) ─────────────────────────────────
 *   - OrdersSlice  : 미배정 인박스 + 배치 그룹 라이프사이클 (assign/sync/unassign/restore)
 *   - BatchesSlice : tasks/equipment + 선택/체인/편집/모달/캐스케이드
 *   - DiffSlice    : compareMode (run vs run diff overlay)
 *   - FiltersSlice : viewFilter/zoomLevel/range/dayWidthScale + violations
 *
 * 외부 import 경로(`@/features/scheduler/store/scheduleStore`)는 변경 없음 — 모든 기존
 * useScheduleStore 사용처는 자동으로 합쳐진 store 를 받는다.
 *
 * 슬라이스가 필요하다면 useScheduleStore.getState() 후 직접 액션을 호출해도 되고,
 * createXxxSlice 로 export 된 타입은 컴포넌트별 selector 작성 시 가독성용으로 사용 가능.
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import { enableMapSet } from "immer";

// Set/Map 을 immer draft 내에서 mutate 하려면 플러그인 활성화가 필요.
// inFlightBatchGroups (Set<string>) 이 Set 이므로 모듈 로드 시 1회 호출.
enableMapSet();

import { createOrdersSlice, type OrdersSlice } from "./slices/ordersSlice";
import { createBatchesSlice, type BatchesSlice } from "./slices/batchesSlice";
import { createDiffSlice, type DiffSlice } from "./slices/diffSlice";
import { createFiltersSlice, type FiltersSlice } from "./slices/filtersSlice";

export type ScheduleStore = OrdersSlice &
  BatchesSlice &
  DiffSlice &
  FiltersSlice;

export const useScheduleStore = create<ScheduleStore>()(
  immer((...a) => ({
    ...createOrdersSlice(...a),
    ...createBatchesSlice(...a),
    ...createDiffSlice(...a),
    ...createFiltersSlice(...a),
  })),
);
