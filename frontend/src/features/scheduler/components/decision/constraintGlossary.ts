/**
 * 제약 카탈로그 — DecisionConstraintsModal 의 활성 제약 chip / 카테고리 헤딩에
 * 표시되는 사람이 읽을 수 있는 설명.
 *
 * 왜 frontend 에 두는가:
 *   - backend ConstraintConfig.notes 컬럼은 비어 있고 (seed 미작성), domain
 *     모듈에도 사람용 설명이 없다. 사용자가 회계사(제조 도메인 비경험) 이므로
 *     constraint_id → korean_name 만으로는 의미 추론이 어려움.
 *   - backend 라운드트립 한 번 더 도는 비용보다 frontend 상수가 압도적으로 빠름.
 *     이 mapping 은 ConstraintConfig 시드 변경이 있어야만 업데이트.
 *
 * 유지보수:
 *   - constraint_id 신설/변경 시 backend/seed_db.py 와 동기화.
 *   - 카테고리 prefix 가 바뀌면 CATEGORY_INFO 도 업데이트.
 */

/** constraint_id prefix → 카테고리 메타. */
export const CATEGORY_INFO: Record<
  string,
  { label: string; description: string }
> = {
  "1": {
    label: "고객/수주",
    description:
      "거래처 우선순위·납기 기준·긴급 변경 — 외부 약속을 지키는 제약",
  },
  "2": {
    label: "공정/외주",
    description:
      "재공 재고·외주 조건·틀단위 생산·61연선 분리 등 공정 운영 규칙",
  },
  "3": {
    label: "색상",
    description:
      "색상별 여척·묶음·설비 그룹·잔량 흑색 소진 — 색상 변경 비용 최소화",
  },
  "4": {
    label: "시간",
    description: "규격·색상교체, 드럼 권취, 용접 등 셋업 시간 모델링",
  },
  "5": {
    label: "설비",
    description: "SQ·연선방식·다심·재질 등 설비별 호환성·할당 규칙",
  },
  "6": {
    label: "일정",
    description: "안전교육·금요일 야간 단축·부재자·공휴일 등 캘린더 제약",
  },
  "7": {
    label: "품질",
    description: "불량 재작업 버퍼·설비 고장 대체 — 품질·가용성 확보",
  },
  "8": {
    label: "재고/조달",
    description: "원자재·테이프/컴파운드 재고와 조달 리드타임 반영",
  },
  "9": {
    label: "라우팅",
    description: "다심 연합·GC/TFR-GV 등 공정 경로 분기 규칙",
  },
  "10": {
    label: "통합",
    description: "SQ·재질·시스·전압·심수 — 여러 카테고리에 걸치는 통합 규칙",
  },
};

/**
 * constraint_id → 짧은 도메인 설명 (1문장).
 *
 * 작성 원칙:
 *   - constraint_name 을 약간 풀어쓴 수준 — 추측은 금지 (AI slop 회피).
 *   - 회계사도 의미 파악 가능하게 도메인 용어는 간단 해설.
 *   - W-* 가중치 항목은 contributions 에만 등장 — chip 에서는 안 보이므로 생략.
 */
export const CONSTRAINT_DESCRIPTIONS: Record<string, string> = {
  "1-1":
    "수주 거래처별로 미리 정해진 우선순위 (P1·P2·P3 등) 를 반영해 배정 순서를 결정",
  "1-2": "수주 납기 + 운송일수 1일 가산 — 출하 마감일 기준",
  "1-3": "긴급 변경 (납기 단축·물량 추가) 발생 시 기존 일정 재조정",
  "2-1": "재공 (WIP) 으로 남은 연선·절연 반제품을 우선 소진해 신규 투입 최소화",
  "2-2":
    "외주 위탁 가능 조건 — 단면적 ≤10SQ 또는 고내화16 (난연성 케이블) 사양",
  "2-3": "드럼 틀(권취 단위) 기준으로 생산량 산정 — 부분 권취 불허",
  "2-4": "61가닥 연선 (대용량 도체) 은 별도 라인·시간으로 분리 처리",
  "3-1": "색상별로 여척 (여유 길이) 가산 — 색상 변경 시 손실분 보전",
  "3-2": "같은 색상 수주는 가능한 한 묶어 연속 생산 — 색상교체 횟수 최소화",
  "3-3": "특정 설비에서 처리 가능한 색상 그룹만 허용 (호기별 호환성)",
  "3-4": "흑색 잔량을 우선 소진해 다음 색상으로 교체 시 손실 최소화",
  "4-1": "규격 (SQ·심수) 변경 시 설비 셋업 시간 — ConstraintConfig 4-1 우선",
  "4-2": "색상 변경 시 시스·절연 교체 시간 — 시스 공정 fallback",
  "4-3": "드럼 권취 (감기) 에 소요되는 시간 — 길이·속도 기반",
  "4-4": "도체 용접 (이음새 처리) 시간 — 라인 정지 분",
  "4-5": "테이핑 (감싸기) 공정의 라인 속도 상한",
  "5-1":
    "도체 단면적 (SQ) 별로 처리 가능한 설비 매칭 — range_min/max 범위 검증",
  "5-2": "연선 방식 (압축 / 원형 / 수밀) 에 따른 설비 분리",
  "5-3": "다심 (2심 이상) 케이블은 다심 전용 설비에 우선 배정",
  "5-4": "나선 (단심 일반) 과 연동선 은 라인 분리",
  "5-5": "TFR-GV (난연 접지선) 는 절연 공정을 생략하고 연선→시스 직행",
  "6-1": "매월 마지막 2주 월요일 안전교육 — 작업 시간 단축 반영",
  "6-2": "금요일 야간 단축 근무 — 마감 시간 조기 종료",
  "6-3": "부재자 (휴가·교육) 가 있는 날은 해당 설비 가용량 감소",
  "6-4": "공휴일·휴무일은 배정 불가",
  "7-1": "불량 발생을 가정한 재작업 버퍼 — 길이·시간 마진 가산",
  "7-2": "설비 고장 시 대체 설비로 자동 이관",
  "8-1": "테이프·컴파운드 (외피 재료) 재고 수준에 따른 투입 제한",
  "8-2": "원자재 조달 리드타임 — 발주 후 입고일까지 대기 시간",
  "8-3": "도체 재질 (CU 동 / AL 알루미늄) 에 맞는 라인 분리",
  "9-1": "다심 케이블은 연합 (combine) 공정으로 분기",
  "9-2": "GC (Ground Cable) 는 B100 설비 사용 불가 — 라우팅 제외",
  "9-3": "TFR-GV 바이패스 라우팅 — 5-5 와 통합 운영",
  "10-1": "SQ 기준 설비 배정 — 5-1 과 통합 (legacy id 호환용)",
  "10-2": "도체 재질 CU/AL 별 설비 분리 — 8-3 의 통합 버전",
  "10-3": "시스 (외피) 재질에 따른 라우팅 분기",
  "10-4": "전압 등급 (저압/고압) 별로 드럼 분류",
  "10-5": "4심 케이블 길이·중량 계산법 — SQ × 심수 가산",
};

/**
 * constraint_id 의 prefix (`1`, `2`, ... `10`) 를 추출.
 * 예: "5-1" → "5", "10-2" → "10", "W-DHARD" → "W"
 */
export function categoryPrefix(constraintId: string): string {
  const idx = constraintId.indexOf("-");
  return idx === -1 ? constraintId : constraintId.slice(0, idx);
}

/** prefix 에 대응하는 카테고리 라벨 — 미지정 prefix 는 "기타". */
export function categoryLabel(prefix: string): string {
  return CATEGORY_INFO[prefix]?.label ?? "기타";
}

/** prefix 에 대응하는 카테고리 설명 한 줄 — 없으면 빈 문자열. */
export function categoryDescription(prefix: string): string {
  return CATEGORY_INFO[prefix]?.description ?? "";
}

/** constraint_id 의 도메인 설명. 미정 항목은 빈 문자열 (tooltip 미표시). */
export function constraintDescription(constraintId: string): string {
  return CONSTRAINT_DESCRIPTIONS[constraintId] ?? "";
}
