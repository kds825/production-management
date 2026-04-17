/**
 * Sweep-line 알고리즘으로 동일 row 에 시간 겹치는 블록에 lane 번호를 할당한다.
 * O(n log n). lane 0 부터 채움. 겹침 없으면 모두 lane 0.
 *
 * 안전망 성격의 유틸리티: 백엔드(CP-SAT)가 동일 설비-시간 겹침을 방지하지만,
 * 엣지 케이스가 슬립할 경우 Y축 분리로 사용자가 두 블록을 모두 볼 수 있도록 한다.
 */
export interface LaneInput {
  id: string | number;
  start: number; // epoch ms
  end: number;
}

export interface LaneOutput extends LaneInput {
  lane: number;
}

/**
 * 입력된 아이템에 lane 번호를 할당한다.
 * - start 오름차순(동시 시작 시 입력 순서) 로 정렬 후 sweep.
 * - 각 lane 의 현재 종료 시각을 추적하고, 새 아이템의 start 가
 *   기존 lane 의 end 보다 크거나 같으면(touch 허용) 해당 lane 에 배치.
 * - 모두 겹치면 새 lane 을 추가.
 */
export function assignLanes(items: LaneInput[]): LaneOutput[] {
  // 원본 순서를 보존한 채로 정렬 — 결과 반환 시 입력 순서대로 lane 매핑
  const indexed = items.map((item, idx) => ({ ...item, _idx: idx }));
  const sorted = [...indexed].sort(
    (a, b) => a.start - b.start || a._idx - b._idx,
  );

  const laneEnds: number[] = [];
  const assigned: Record<number, number> = {};

  for (const item of sorted) {
    let lane = -1;
    for (let i = 0; i < laneEnds.length; i++) {
      if (laneEnds[i] <= item.start) {
        lane = i;
        laneEnds[i] = item.end;
        break;
      }
    }
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(item.end);
    }
    assigned[item._idx] = lane;
  }

  return items.map((item, idx) => ({ ...item, lane: assigned[idx] }));
}

/** lane 개수(최대 lane + 1). 빈 배열은 1 반환. */
export function getLaneCount(items: LaneOutput[]): number {
  if (items.length === 0) return 1;
  return Math.max(...items.map((x) => x.lane)) + 1;
}
