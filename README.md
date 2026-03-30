# KBI 생산계획 스케줄러

KBI 코스모링크 전선 공장의 생산계획을 자동화하는 웹 애플리케이션.
ERP 수주 + 재공실사 데이터를 업로드하면 공정별 작업지시서(Excel)를 생성하고, 35개 제약조건 기반으로 설비별 간트 차트에 자동 배열합니다.

---

## 기술 스택

| 영역      | 기술                                           |
| --------- | ---------------------------------------------- |
| Frontend  | Next.js 16, React 19, TypeScript, Tailwind CSS |
| 간트 차트 | @dnd-kit/core (CSS Grid 기반 커스텀 간트)      |
| 상태관리  | Zustand + Immer                                |
| Backend   | FastAPI (Python 3.11+)                         |
| DB        | PostgreSQL (Supabase)                          |
| Excel     | openpyxl (생성) + xlrd (ERP 파싱)              |

---

## 실행 방법

### 1. 환경 변수 설정

```bash
# backend/.env
DATABASE_URL=postgresql://user:pass@host:port/dbname
```

### 2. 백엔드 실행

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3. 프론트엔드 실행

```bash
cd frontend
npm install
npm run dev
```

### 4. 접속

브라우저에서 http://localhost:3000 접속

> 마스터 데이터(설비, 선속, 제약조건 등)는 Supabase DB에 이미 적재되어 있습니다.
> 별도 시드 작업 불필요.

---

## 시연 플로우

```
┌─────────────────────────────────────────────────────────┐
│  1. 생산계획등록 (/plan-register)                        │
│     ① 계획 기준일자 선택                                 │
│     ② 재공실사 파일 업로드 (demo_wip_data.xlsx)          │
│     ③ ERP 파일 업로드 (3.25진행및대기.xls)               │
│     → "작업지시서 생성" 클릭 = Stage 1 실행              │
├─────────────────────────────────────────────────────────┤
│  2. 스케줄링 검토 (/scheduling-review)                   │
│     → 실행 버전 선택 → 공정별 탭으로 배치 확인           │
│     → WIP 매칭 확인 (재고 사용 → 클릭으로 WIP 이동)     │
│     → Excel 다운로드                                     │
│     → 배치 수정/추가/삭제 가능                           │
├─────────────────────────────────────────────────────────┤
│  3. 간트 스케줄러 (/scheduler)                           │
│     → "자동배열" 클릭 = Stage 2 실행                     │
│     → 설비(Y축) x 날짜(X축) 간트 차트                    │
│     → 블록 클릭 → 배치 내 수주 상세 목록                 │
│     → 블록 우클릭 → 배치 분할                            │
│     → D&D로 블록 이동                                    │
│     → 필터: 전체/저압만/고압만/공정별                     │
├─────────────────────────────────────────────────────────┤
│  4. 감사 추적 (/audit)                                   │
│     → Stage 1 WIP 매칭 결정 로그                         │
│     → Stage 2 설비 배정 결정 로그                        │
└─────────────────────────────────────────────────────────┘
```

---

## 파이프라인 아키텍처

### Stage 1: 배치 생성 (batch_grouping.py)

```
ERP 수주 파일 (.xls)
    │
    ▼
┌──────────────────┐
│  erp_parser.py   │  ERP 파싱 → sales_order 테이블
└──────────────────┘
    │
    ▼
┌──────────────────┐
│  wip_parser.py   │  재공실사 파싱 → wip_inventory 테이블
│  wip_matching.py │  WIP ↔ 수주 매칭 (SQ + 색상 기준)
└──────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────┐
│  batch_grouping.py                                │
│                                                    │
│  수주 1건 → 공정별 production_batch 생성           │
│  ┌─────────────────────────────────────────┐      │
│  │ 적용 제약조건:                            │      │
│  │  2-2  외주 자동분류 (SQ<=10, 고내화)      │      │
│  │  3-1  여척 계산 (7m + 시료 10m)           │      │
│  │  3-4  잔량 흑색 소진 (비활성 가능)         │      │
│  │  5-2  연선방식 격리 (정렬)                │      │
│  │  5-3  다심 우선 완성 (정렬)               │      │
│  │  5-5  TFR-GV 절연생략 (SQ<=25)           │      │
│  │  7-1  불량 재작업 버퍼 (+5%)              │      │
│  │  10-4 전압별 드럼 분류 (정렬)             │      │
│  │  10-5 4심 계산법                          │      │
│  └─────────────────────────────────────────┘      │
│                                                    │
│  batch_group 부여: 같은 (공정, SQ) = 1그룹         │
│  저압시스: 색상으로 A100(갈/회) / A120(흑/청) 분리  │
│  연선: 전압으로 저압연선 / 고압연선 분리             │
└──────────────────────────────────────────────────┘
    │
    ▼
  production_batch 테이블 (431건, 54 배치그룹)
  Excel 작업지시서 (8시트: 연선/B100/A100/A120/연합/CV절연/A150시스/고압연선)
```

### Stage 2: 자동 스케줄링 (schedule_optimizer.py)

```
production_batch (batch_group 단위)
    │
    ▼
┌──────────────────────────────────────────────────┐
│  schedule_optimizer.py                            │
│                                                    │
│  1. 공정 순서 정렬 (연선→절연→연합→시스)           │
│  2. WIP 매칭 공정 스킵 (wip_complete)              │
│  3. batch_group당 1 schedule_task 생성             │
│                                                    │
│  ┌─────────────────────────────────────────┐      │
│  │ 적용 제약조건:                            │      │
│  │  4-1  동일SQ 셋업스킵 (0분)              │      │
│  │  4-2  색상교체 시간 (+120분, 시스)        │      │
│  │  4-3  드럼 권취 시간                      │      │
│  │  4-4  스플라이스 용접 시간                │      │
│  │  4-5  T/P 테이핑 속도                     │      │
│  │  5-1  SQ→설비 배정 (T6B0=≤50, 54BO=70+) │      │
│  │  10-2 CU/AL 재질 분리                     │      │
│  │  10-3 시스 재질 라우팅                    │      │
│  └─────────────────────────────────────────┘      │
│                                                    │
│  ┌─────────────────────────────────────────┐      │
│  │ 스케줄링 규칙:                            │      │
│  │  - 선행공정 완료 후 후공정 시작            │      │
│  │  - 시스: 절연 전체 완료 후 모아서 투입     │      │
│  │  - 고압시스: +20hr 건조대기                │      │
│  │  - 같은 SQ → 같은 연선 설비 고정          │      │
│  │  - 소선경 그루핑 (같은 소선경 연속 배치)   │      │
│  │  - 종료시간 정각 올림 (09:48→10:00)       │      │
│  └─────────────────────────────────────────┘      │
│                                                    │
│  calendar_engine.py:                               │
│    월~목: 22h, 금: 14h, 토/일: 0h                  │
│    안전교육: 매월 마지막 2주 월요일 -2h             │
│    공휴일: 0h                                      │
│    부동시간: 매일 -2h                              │
└──────────────────────────────────────────────────┘
    │
    ▼
  schedule_task 테이블 (52건 = 간트 블록 52개)
```

---

## 최적화 방식: 규칙 기반 휴리스틱

ML/AI 모델이 아닌 **규칙 기반 그리디 최적화(Heuristic)**입니다.

```
Stage 2 스케줄링 알고리즘:
  1. 공정 순서 정렬 (연선→절연→연합→시스)
  2. 같은 공정 내 SQ 내림차순 정렬
  3. 각 batch_group에 대해:
     a. 적합 설비 후보 탐색 (SQ범위, 재질, 색상 조건)
     b. 설비별 가용 슬롯 탐색 (타임라인 + 선행공정 완료 시각)
     c. 가장 빠른 시작 가능 슬롯 선택 (그리디)
     d. 셋업/색상교체/용접 시간 가산
     e. calendar_engine으로 종료 시각 계산 (주말/공휴일/부동시간 반영)
  4. schedule_task 생성 → 타임라인 갱신 → 다음 그룹
```

### 제약조건 동적 제어 (`constraint_config` 테이블)

프론트 `/master/constraints` 페이지에서 ON/OFF 토글 가능. 제어 수준이 2단계:

| 제어 수준       | 설명                                            | 예시                                         | 토글 효과                                           |
| --------------- | ----------------------------------------------- | -------------------------------------------- | --------------------------------------------------- |
| **DB 파라미터** | `is_enabled` + `params_json` 모두 코드에서 읽음 | 3-1 여척(7m), 3-4 흑색소진, 7-1 불량버퍼(5%) | ✅ OFF 시 완전 비활성화, 파라미터 값 변경 즉시 반영 |
| **코드 로직**   | 로직은 코드에 하드코딩, `is_enabled`는 미참조   | 2-2 외주, 2-4 61연선, 5-5 TFR-GV             | ⚠️ 토글해도 동작 변화 없음 (코드 수정 필요)         |

> Stage 1 또는 Stage 2를 재실행하면 변경된 설정이 반영됩니다.

### 동적 제어 가능 제약조건 상세

| ID  | 제약조건       | params_json 키                     | 기본값    | 설명                    |
| --- | -------------- | ---------------------------------- | --------- | ----------------------- |
| 3-1 | 여척           | `extra_length_m`, `sample_extra_m` | 7m, 10m   | 색상 전환 손실 + 시료   |
| 3-4 | 잔량 흑색 소진 | `remnant_threshold_m`              | 200m      | 이하 배치는 흑색 강제   |
| 4-4 | 용접 시간      | `welding_min`                      | 30분      | 다른 수주 간 스플라이스 |
| 7-1 | 불량 버퍼      | `defect_buffer_pct`                | 0.05 (5%) | 생산 길이에 가산        |

---

## 제약조건 전체 목록 (35/38 적용)

### 납기/우선순위 (3건)

| ID  | 제약조건             | 적용 단계 | 코드 위치                                        |
| --- | -------------------- | --------- | ------------------------------------------------ |
| 1-1 | 거래처 우선순위      | Stage 1,2 | batch_grouping (정렬), schedule_optimizer (정렬) |
| 1-2 | 납기 기준(도착/출하) | Stage 1,2 | batch_grouping (정렬), schedule_optimizer (정렬) |
| 1-3 | 긴급 변경 대응       | 수동      | D&D로 블록 이동                                  |

### SM수량/재고 (4건)

| ID  | 제약조건                      | 적용 단계 | 코드 위치                        |
| --- | ----------------------------- | --------- | -------------------------------- |
| 2-1 | 재공 활용(연선/절연 재고우선) | Stage 1   | wip_matching, batch_grouping     |
| 2-2 | 외주 조건(SQ<=10, 고내화16)   | Stage 1   | batch_grouping                   |
| 2-3 | 틀단위 기준 생산              | Stage 1   | batch_group으로 대체             |
| 2-4 | 61연선 분리                   | Stage 1   | batch_grouping (7연선 코어 선행) |

### 색상관리 (3건)

| ID  | 제약조건                      | 적용 단계 | 코드 위치                             |
| --- | ----------------------------- | --------- | ------------------------------------- |
| 3-1 | 색상별 여척 추가 (7m+시료10m) | Stage 1   | batch_grouping                        |
| 3-2 | 색상 묶음 배치                | Stage 1   | batch_grouping (정렬 키)              |
| 3-3 | 설비별 색상그룹 제한          | Stage 1   | batch_grouping (A100=갈회, A120=흑청) |

### 시간/속도 (5건)

| ID  | 제약조건                   | 적용 단계 | 코드 위치          |
| --- | -------------------------- | --------- | ------------------ |
| 4-1 | 규격교체 시간 (동일SQ=0분) | Stage 2   | schedule_optimizer |
| 4-2 | 색상교체 시간 (+120분)     | Stage 2   | schedule_optimizer |
| 4-3 | 드럼 권취 시간             | Stage 2   | schedule_optimizer |
| 4-4 | 용접 시간                  | Stage 2   | schedule_optimizer |
| 4-5 | 테이핑 속도 제한           | Stage 2   | schedule_optimizer |

### 설비배정 (6건)

| ID   | 제약조건                      | 적용 단계 | 코드 위치                                  |
| ---- | ----------------------------- | --------- | ------------------------------------------ |
| 5-1  | SQ 기준 설비 배정             | Stage 2   | schedule_optimizer (\_SQ_TO_WIRE_DIAMETER) |
| 5-2  | 연선방식 구분(압축/원형/수밀) | Stage 1   | batch_grouping (정렬 키)                   |
| 5-3  | 다심 우선배치                 | Stage 1   | batch_grouping (정렬 키)                   |
| 5-5  | TFR-GV 절연 생략              | Stage 1   | batch_grouping (skip_stranding)            |
| 10-3 | 시스 재질 라우팅              | Stage 2   | schedule_optimizer                         |
| 10-4 | 전압별 드럼 분류              | Stage 1   | batch_grouping (정렬 키)                   |

### 가동시간 (4건)

| ID  | 제약조건                       | 적용 단계 | 코드 위치       |
| --- | ------------------------------ | --------- | --------------- |
| 6-1 | 안전교육(매월 마지막2주 월-2h) | Stage 2   | calendar_engine |
| 6-2 | 금요일 야간 단축 (14h)         | Stage 2   | calendar_engine |
| 6-4 | 공휴일/휴무 (0h)               | Stage 2   | calendar_engine |
| —   | 토/일 미가동 (0h)              | Stage 2   | calendar_engine |

### 기타 (4건)

| ID   | 제약조건               | 적용 단계 | 코드 위치                              |
| ---- | ---------------------- | --------- | -------------------------------------- |
| 7-1  | 불량 재작업 버퍼 (+5%) | Stage 1   | batch_grouping                         |
| 9-1  | 선행공정 완료 체크     | Stage 2   | schedule_optimizer (process_end_by_sq) |
| 10-2 | CU/AL 재질 분리        | Stage 2   | schedule_optimizer                     |
| 10-5 | 4심 계산법             | Stage 1   | batch_grouping                         |

### 미적용 (3건)

| ID  | 제약조건         | 사유             |
| --- | ---------------- | ---------------- |
| 5-4 | 나선/연동선 별도 | PoC 범위 밖      |
| 9-2 | GC 라우팅 제외   | 해당 데이터 없음 |
| 6-3 | 부재자 계획 반영 | PoC 미적용       |

---

## 공정 프로세스

```
저압 1C:   연선 → 절연(B100) → 시스(A100/A120)
저압 2-4C: 연선 → 절연(B100) → 연합 → 시스
고압:      연선 → 고압절연(CV) → [T/P → 연합 →] 고압시스
TFR-GV:   연선 → 시스 (절연 없음, SQ<=25은 연선도 생략)
61연선:   7연선코어(T6B0) → 61연선완성(54BO) — 300SQ+ 2단계
```

---

## API 엔드포인트

### 파이프라인

| Method | Path                                    | 설명                       |
| ------ | --------------------------------------- | -------------------------- |
| POST   | `/api/pipeline/stage1`                  | ERP+WIP 업로드 → 배치 생성 |
| GET    | `/api/pipeline/stage1/{run}/batches`    | 배치 목록 JSON             |
| GET    | `/api/pipeline/stage1/{run}/export`     | Excel 작업지시서 다운로드  |
| POST   | `/api/pipeline/stage2`                  | 자동 스케줄링              |
| GET    | `/api/pipeline/runs`                    | 실행 이력                  |
| GET    | `/api/pipeline/wip-template`            | 재공실사 템플릿            |
| GET    | `/api/pipeline/batch-group/{bg}/orders` | 배치그룹 내 수주 목록      |
| POST   | `/api/pipeline/batch-group/{bg}/split`  | 배치 분할                  |
| PATCH  | `/api/pipeline/batch/{id}`              | 배치 수정                  |

### 스케줄

| Method | Path                        | 설명             |
| ------ | --------------------------- | ---------------- |
| GET    | `/api/schedules/tasks`      | 간트 태스크 목록 |
| POST   | `/api/schedules/tasks`      | 태스크 추가      |
| PUT    | `/api/schedules/tasks/{id}` | 태스크 수정      |
| DELETE | `/api/schedules/tasks/{id}` | 태스크 삭제      |

### 마스터/감사

| Method  | Path                            | 설명               |
| ------- | ------------------------------- | ------------------ |
| GET     | `/api/equipment`                | 설비 목록          |
| GET/PUT | `/api/master/{table}`           | 마스터 데이터 CRUD |
| GET     | `/api/audit/logs/{run}`         | 감사 로그          |
| GET     | `/api/audit/explain/{batch_id}` | AI 결정 설명       |

---

## 프로젝트 구조

```
backend/
  app/
    services/
      erp_parser.py          # ERP .xls 파싱
      wip_parser.py          # 재공실사 파싱
      wip_matching.py        # WIP ↔ 수주 매칭
      batch_grouping.py      # Stage 1: 배치 생성 (제약조건 적용)
      schedule_optimizer.py  # Stage 2: 자동 스케줄링 (제약조건 적용)
      calendar_engine.py     # 가동시간 계산 (주말/공휴일/안전교육)
      excel_exporter.py      # Excel 작업지시서 생성
    presentation/routes/
      plan_pipeline.py       # Stage 1/2 API
      schedules.py           # 간트 태스크 CRUD
    infrastructure/models/   # SQLAlchemy ORM 모델

frontend/src/
  app/(main)/
    plan-register/           # 생산계획 등록 (파일 업로드)
    scheduling-review/       # 스케줄링 검토 (배치 테이블, WIP)
    scheduler/               # 간트 차트 (D&D, 블록 분할)
    audit/                   # 감사 추적
    master/                  # 마스터 데이터 관리
  features/
    scheduler/components/
      SchedulerView.tsx      # 간트 메인 뷰
      GanttTaskBlock.tsx     # 블록 (색상별 배경)
      ViewFilter.tsx         # 전체/저압/고압/공정별 필터
      BatchSplitModal.tsx    # 배치 분할 모달
    scheduling-review/
      store/                 # 배치 상태관리
      components/            # 테이블, WIP, AI 분석
```

---

## 환경 변수

| 변수                  | 설명                                              |
| --------------------- | ------------------------------------------------- |
| `DATABASE_URL`        | PostgreSQL 연결 (Supabase)                        |
| `NEXT_PUBLIC_API_URL` | 백엔드 API 주소 (기본: http://localhost:8000/api) |
| `ANTHROPIC_API_KEY`   | LLM 결정 설명 (선택)                              |

---

Internal use only (삼일PwC - KBI PoC)
