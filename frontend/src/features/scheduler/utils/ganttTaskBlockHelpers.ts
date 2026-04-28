/**
 * GanttTaskBlock 의 pure helper 함수들 — 분해 후 동작 동일.
 *
 * 추출 대상 (Task 1.18, plan §F-4):
 * - isFrozenStatus
 * - splitByWeekends
 * - isSheathEquipment
 * - getSheathColor (+ SHEATH_COLOR_MAP)
 * - snapToHour
 */

const MS_PER_HOUR = 60 * 60 * 1000;

/** frozen 배치(진행중/완료)는 드래그 불가 */
export function isFrozenStatus(status: string): boolean {
  return status === "in_progress" || status === "completed";
}

/**
 * 주말(토 00:00 ~ 월 00:00)을 건너뛰어 연속 평일 구간 배열을 반환한다.
 * 블록이 금요일을 넘어 이어지면 금요일 자정(토 00:00)에서 끊고
 * 다음 월요일 00:00에 새 구간을 시작한다.
 */
export function splitByWeekends(
  startTs: number,
  endTs: number,
): { start: number; end: number }[] {
  const segments: { start: number; end: number }[] = [];
  let cur = startTs;

  while (cur < endTs) {
    const day = new Date(cur).getDay(); // 0=Sun, 6=Sat

    // 주말이면 월요일 00:00으로 이동
    if (day === 6) {
      const mon = new Date(cur);
      mon.setHours(0, 0, 0, 0);
      mon.setDate(mon.getDate() + 2);
      cur = mon.getTime();
      continue;
    }
    if (day === 0) {
      const mon = new Date(cur);
      mon.setHours(0, 0, 0, 0);
      mon.setDate(mon.getDate() + 1);
      cur = mon.getTime();
      continue;
    }

    // 다음 토요일 00:00 계산 (6 - day: Mon=5, Tue=4, ..., Fri=1)
    const nextSat = new Date(cur);
    nextSat.setHours(0, 0, 0, 0);
    nextSat.setDate(nextSat.getDate() + (6 - day));

    const segEnd = Math.min(endTs, nextSat.getTime());
    if (segEnd > cur) segments.push({ start: cur, end: segEnd });

    cur = segEnd;
    // 토요일에 도달하면 월요일로 점프
    if (cur < endTs && cur === nextSat.getTime()) {
      const mon = new Date(cur);
      mon.setDate(mon.getDate() + 2);
      cur = mon.getTime();
    }
  }

  return segments.length > 0 ? segments : [{ start: startTs, end: endTs }];
}

/** 시스 공정(SH-*) 설비의 sheath_color → 블록 배경색 매핑 */
const SHEATH_COLOR_MAP: Record<string, string> = {
  흑: "var(--neutral-text-primary)",
  갈: "var(--status-warning-text)",
  회: "var(--color-text-secondary)",
  청: "var(--status-info-text)",
  녹: "var(--status-success-text-deep)",
  황: "#B45309",
  "흑/적": "var(--color-brand-primary)",
};

/**
 * 시스 설비 판별: equipment_id 가 "SH-" 로 시작하면 시스 공정으로 간주.
 * 기존에는 명시 set(SH-A100/SH-A120)을 사용했으나 SH-A150(고압시스), SH-B100 등
 * 신규 설비가 추가될 때마다 라벨/색상이 회귀하는 버그가 있어 prefix 검사로 전환.
 */
export function isSheathEquipment(
  equipmentId: string | undefined | null,
): boolean {
  return typeof equipmentId === "string" && equipmentId.startsWith("SH-");
}

/** 시스 설비일 때 task.color 기반 배경색 반환, 아니면 null */
export function getSheathColor(
  equipmentId: string,
  color: string,
): string | null {
  if (!isSheathEquipment(equipmentId)) return null;
  if (!color) return null;
  // 정확한 키 매칭 우선
  if (SHEATH_COLOR_MAP[color]) return SHEATH_COLOR_MAP[color];
  // 부분 매칭: 색상 문자열에 키워드가 포함되어 있으면 적용
  for (const [key, hex] of Object.entries(SHEATH_COLOR_MAP)) {
    if (color.includes(key)) return hex;
  }
  return null;
}

export function snapToHour(ts: number): number {
  return Math.round(ts / MS_PER_HOUR) * MS_PER_HOUR;
}
