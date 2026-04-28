// plan-register 페이지에서 공유하는 타입 정의.
// page.tsx 가 1,910 줄로 비대해져 분할(Phase 1 F-1.1)하면서 추출.
// - 컴포넌트-로컬 props 인터페이스(예: DiffSummaryPanelProps)는 각 컴포넌트
//   파일에 그대로 두고, 여기에는 페이지 전반에서 공유되는 데이터 모델만 둔다.

// 기존 plan-register/types/index.ts 에 있던 도메인 타입을 그대로 흡수.
// (단일 types.ts 로 통합, types/ 디렉토리 폐기 — 깊이 ≤ 3 유지)
export type { ProductionBatch } from "@/features/scheduler/types";

export type VoltageType = "전체" | "고압" | "저압";
export type EquipmentGroup = "연선" | "B100" | "A100" | "A120" | "T/P";

export interface UploadedFile {
  name: string;
  size: number;
  uploadedAt: Date;
}

export interface WipFile {
  name: string;
  size: number;
  uploadedAt: Date;
  file: File; // 실제 File 객체 — Stage 1 API에 전송용
}

// Stage 1 API response types
export interface ParsedOrder {
  order_id: string;
  product_code: string;
  quantity: number;
  due_date: string;
}

// /stage1 및 /stage1/update 응답의 batches 필드. 백엔드 create_batches() 는 개별
// 배치 행이 아니라 집계 요약만 반환한다. 과거 ParsedBatch[] 로 잘못 타이핑되어
// BatchGridWithFrozen 이 .map() 에서 런타임 TypeError 를 일으켰다.
export interface BatchSummary {
  total_batches: number;
  by_process: Record<string, number>;
  warnings?: string[];
  outsource_count?: number;
}

// T2a-2: /stage1/update 가 응답에 별도로 내려주는 "이번 파일로 추가된 주문에서
// 나온 planned 배치" 집계. 전체 batches(재생성 총계)와 구분해 UI 표기.
export interface NewFromFileSummary {
  total_batches: number;
  by_process: Record<string, number>;
}

// T2a: /stage1/update 응답의 frozen_batches 원소. 진행중/완료/wip_complete 상태의
// 기존 배치를 재생성된 ParsedBatch 리스트와 함께 렌더링하기 위해 사용한다.
export interface FrozenBatch {
  batch_id: number;
  batch_group: string | null;
  process_name: string;
  status: "in_progress" | "completed" | "wip_complete";
  customer_name: string | null;
  due_date: string | null;
  item_code: string | null;
  product_group: string | null;
  voltage: string | null;
  sq_mm2: number | null;
  sheath_color: string | null;
  drum_count: number | null;
  total_length_m: number | null;
  sales_order_id: string | null;
  sales_order_line: number | null;
  equipment_code: string | null;
}

// T2b: Full 모드에서 새 파일과 기존 수주 리스트를 대사한 order_id 레벨 분류.
export interface OrderDiffSummary {
  added: number;
  updated: number;
  deleted: number;
  preserved_frozen: number;
}

export interface SplitChunk {
  lot_index: number;
  order_count: number;
  total_m: number;
  min_due: string;
  max_due: string;
  order_ids: string[];
  batch_ids: number[];
  has_urgent?: boolean;
  min_priority?: number;
  days_until_due?: number;
}

export interface SplitCandidate {
  batch_group: string;
  equipment_code: string | null;
  sq_mm2: number;
  lot_count: number;
  total_length_m: number;
  proposed_splits: SplitChunk[];
  gaps_days: number[];
  equipment_load_hours: number;
  auto_split_recommended?: boolean;
  urgency_reason?: string;
}

export type UploadMode = "full" | "incremental";

export interface Stage1Result {
  run_label: string;
  parsed_orders: ParsedOrder[];
  batches: BatchSummary;
  // T2a-2: /stage1/update 에서만 내려줌. 이 파일로 실제 추가된 주문 → planned 배치.
  new_from_file?: NewFromFileSummary | null;
  warnings: string[];
  split_candidates?: SplitCandidate[];
  // incremental update 결과 요약 (stage1/update 응답에만 포함될 수 있음)
  added_orders?: number;
  created_batch_groups?: number;
  preserved_batches?: number;
  auto_split_count?: number;
  // P6 긴급수주 diff — /stage1/update 응답에만 포함 (전체 교체 모드에선 null)
  // change_set_id가 있으면 /schedules/change-sets/{id}/diff 로 diff 조회 가능
  change_set_id?: string | null;
  // snapshot_persisted=false 이면 이 변경은 자동 롤백 불가 (사용자 경고 필수)
  snapshot_persisted?: boolean;
  snapshot_count_before?: number;
  snapshot_count_after?: number;
  // T2a: 진행중/완료/wip_complete 로 보존된 배치. 재생성된 batches 와 병합 렌더링.
  frozen_batches?: FrozenBatch[];
  // T2b: Full 모드에서만 세팅. order_id 레벨 대사 결과.
  diff_summary?: OrderDiffSummary | null;
  // 응답 기반 분기(Full diff 패널 조건부 렌더)를 위해 노출.
  upload_mode?: UploadMode;
}

export interface BatchStatusSummary {
  total_batches: number;
  completed: number;
  in_progress: number;
  scheduled: number;
  wip_complete: number;
  planned: number;
  frozen_count: number;
  frozen_wip_count: number;
  available_wip_count: number;
}
