/**
 * scheduler-view 내부 공유 상수.
 *
 * LANE_HEIGHT 는 외부(ChainHighlightOverlay) 에서도 import 하므로 SchedulerView.tsx 의
 * thin re-export 가 동일 심볼을 노출한다 (re-export 경로 보존).
 */
import { ROW_HEIGHT } from "../../utils/ganttUtils";

/**
 * 한 lane(Y축 칸) 의 높이. ROW_HEIGHT 와 동일하게 두어
 * 단일 lane(겹침 없음) 인 경우 기존 레이아웃과 동일하게 보이고,
 * 겹치는 블록이 있으면 lane 개수만큼 row 가 세로로 늘어난다.
 */
export const LANE_HEIGHT = ROW_HEIGHT;

/** 주말 컬럼 접힘 시 너비(px) */
export const WEEKEND_COLLAPSED_WIDTH = 8;
