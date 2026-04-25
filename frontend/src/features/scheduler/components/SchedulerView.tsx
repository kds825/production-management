/**
 * SchedulerView — thin re-export.
 *
 * Week 7 Task 7B.2 기준으로 본문은 `./scheduler-view/{index,Toolbar,GanttGrid,TaskRow,DiffOverlay}.tsx`
 * 로 이전되었다. 외부 import 경로(`@/features/scheduler/components/SchedulerView`) 와
 * 공개 export 심볼(SchedulerView / equipmentMatchesGroup / getProposalFor / LANE_HEIGHT)
 * 는 모두 보존하므로 모든 기존 사용처는 변경 없이 동작한다.
 *
 * 새 코드는 가급적 `./scheduler-view/...` 의 정확한 모듈을 직접 import 하는 것이 좋다.
 */
export { SchedulerView } from "./scheduler-view";
export {
  TaskRow,
  equipmentMatchesGroup,
  getProposalFor,
  type SharedSelection,
} from "./scheduler-view/TaskRow";
export { LANE_HEIGHT } from "./scheduler-view/constants";
