# KBI 생산계획 스케줄러

KBI 코스모링크 전선 공장의 생산계획을 자동화하는 웹 도구입니다.

ERP 수주 데이터 + 재공실사 데이터를 업로드하면 공정별 Excel 계획서를 생성하고, 설비별 간트 차트에 자동 배치합니다.

---

## 기술 스택

| 영역     | 기술                                           |
| -------- | ---------------------------------------------- |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS |
| 스케줄러 | @dnd-kit/core (커스텀 CSS Grid Gantt)          |
| 상태관리 | Zustand + Immer                                |
| Backend  | FastAPI (Python 3.11+)                         |
| DB       | SQLite (SQLAlchemy ORM)                        |
| Excel    | openpyxl (생성) + xlrd (ERP 파싱)              |

---

## 파이프라인 흐름

```
[plan-register 페이지]
  1. 기준일자 선택 (default: KST 당일)
  2. 재공실사 파일 업로드 (.xlsx, 템플릿 다운로드 가능)
  3. ERP 수주 파일 업로드 (.xls)
       ↓
[Stage 1 — POST /api/pipeline/stage1]
  ERP 파싱 → 진행/대기 중복 제거 → WIP 파싱 → WIP 매칭 → 배치 생성
       ↓
[Excel 다운로드 — GET /api/pipeline/stage1/{run}/export]
  공정별 시트 (연선, B100, A100, A120, 고압연선, CV절연, A150시스)
       ↓
[Stage 2 — POST /api/pipeline/stage2]
  자동 스케줄링 (base_date 기준) → 간트 차트 배치
       ↓
[scheduler 페이지]
  설비(Y축) x 날짜(X축) 간트 차트 → 드래그&드롭 수정
```

---

## 사전 준비

- **Node.js** 20+, **Python** 3.11+, **npm** 10+

---

## 설치 및 실행

### Backend

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# DB 초기화 + 시드 데이터
python seed_db.py

# 서버 실행
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

브라우저에서 http://localhost:3000 접속.

---

## 프로젝트 구조

```
KBI-Production-Management/
├── backend/
│   ├── app/
│   │   ├── infrastructure/      # DB 모델, 세션
│   │   │   ├── models/          # SQLAlchemy 모델
│   │   │   └── database.py
│   │   ├── presentation/        # API 라우트, 스키마
│   │   │   └── routes/
│   │   │       ├── plan_pipeline.py   # Stage 1/2 파이프라인
│   │   │       ├── schedules.py       # 간트 태스크 CRUD
│   │   │       └── equipment.py       # 설비 마스터
│   │   └── services/            # 비즈니스 로직
│   │       ├── erp_parser.py          # ERP .xls 파싱
│   │       ├── wip_parser.py          # 재공실사 .xlsx 파싱
│   │       ├── wip_matching.py        # WIP ↔ 수주 매칭
│   │       ├── wip_template.py        # 재공실사 템플릿 생성
│   │       ├── batch_grouping.py      # 수주 → 공정별 배치 변환
│   │       ├── excel_exporter.py      # 공정별 Excel 계획서 생성
│   │       ├── schedule_optimizer.py  # 자동 스케줄링 (간트 배치)
│   │       └── calendar_engine.py     # 주말/공휴일 제외 시간 계산
│   ├── seed_db.py               # 마스터 데이터 시드
│   └── requirements.txt
│
├── frontend/src/
│   ├── app/(main)/
│   │   ├── plan-register/       # 생산계획 등록 (파일 업로드)
│   │   ├── scheduler/           # 간트 스케줄러
│   │   └── scheduling-review/   # 스케줄링 검토
│   └── features/
│       └── scheduler/components/
│           ├── SchedulerView.tsx     # 간트 메인 뷰
│           ├── GanttTaskBlock.tsx    # 작업 블록 (D&D)
│           └── EquipmentSidebar.tsx  # 설비 사이드바
│
└── Documents/                   # 참고 문서
```

---

## 핵심 구현 사항

### 공정 프로세스

```
저압 1C:   신선 → 연선 → 1중절연(B100) → 시스(A100/A120)
저압 2-4C: 신선 → 연선 → 1중절연(B100) → 연합 → 시스
고압:      신선 → 연선 → 3중절연(CV) → [T/P → 연합 →] 시스
TFR-GV:   신선 → 연선 → 시스 (절연 없음, SQ<=25은 연선 생략)
61연선:   7연선코어(T6B0) → 61연선완성(54BO) — 300SQ+ 2단계 처리
```

### 제약조건 (constraint_config)

| ID   | 제약조건                         | 구현 상태                  |
| ---- | -------------------------------- | -------------------------- |
| 2-2  | 외주 자동분류 (SQ<=10, 고내화)   | 구현                       |
| 2-3  | 틀단위 분할 (lot_stranding 기준) | 구현                       |
| 2-4  | 61연선 분리 (300SQ+ 2단계)       | 구현                       |
| 3-1  | 여척 계산 (7m + 시료 10m)        | 구현                       |
| 3-4  | 잔량 흑색 소진                   | 구현 (is_enabled로 on/off) |
| 4-1  | 동일SQ 셋업 스킵                 | 구현                       |
| 4-2  | 색상교체 시간 (+120분)           | 구현                       |
| 4-4  | 스플라이스 용접 시간             | 구현                       |
| 5-2  | 연선방식 격리                    | 구현 (정렬 키)             |
| 5-3  | 다심 우선 완성                   | 구현 (정렬 키)             |
| 7-1  | 불량 재작업 버퍼 (5%)            | 구현                       |
| 10-4 | 전압별 드럼 분류                 | 구현 (정렬 키)             |

### 연선 스케줄 규칙

1. **SQ 범위 분류**: T6B0=16~50SQ (7연선), 54BO=70~800SQ (19/37/61연선)
2. **같은 SQ → 같은 설비** (19연선 이상, 연선 공정만)
3. **소선경 그루핑**: 같은 소선경 SQ를 같은 설비에 연속 배치
4. **색상교체 최소화**: 시스 공정에서 색상 변경 시 120분 추가

### WIP 매칭

- Excel 계획서: 모든 시트에 표시 + "절연재고 사용" / "연선재고 사용" 비고
- 간트 차트: WIP 매칭 공정 배치는 스케줄 미반영 (wip_complete)
  - 절연재고 → 신선/연선/절연 스킵
  - 연선재고 → 신선/연선 스킵

### 시스 설비 분류

- A120: 흑/청 색상
- A100: 나머지 색상 (갈, 회, 녹/황 등)

---

## API 엔드포인트

| Method | Path                                | 설명                       |
| ------ | ----------------------------------- | -------------------------- |
| POST   | `/api/pipeline/stage1`              | ERP+WIP 업로드 → 배치 생성 |
| GET    | `/api/pipeline/stage1/{run}/export` | Excel 계획서 다운로드      |
| POST   | `/api/pipeline/stage2`              | 자동 스케줄링              |
| GET    | `/api/pipeline/wip-template`        | 재공실사 템플릿 다운로드   |
| GET    | `/api/pipeline/runs`                | 계획 실행 이력             |
| GET    | `/api/schedules/tasks`              | 간트 태스크 목록           |
| POST   | `/api/schedules/tasks`              | 태스크 추가                |
| PUT    | `/api/schedules/tasks/{id}`         | 태스크 수정                |
| DELETE | `/api/schedules/tasks/{id}`         | 태스크 삭제                |
| GET    | `/api/equipment`                    | 설비 목록                  |

### Stage 1 파라미터

```
POST /api/pipeline/stage1  (multipart/form-data)
  erp_file: .xls (필수)
  wip_file: .xlsx (선택, 재공실사 템플릿)
  date_from: YYYYMMDD (선택, 납기 시작일)
  date_to: YYYYMMDD (선택, 납기 종료일)
```

### Stage 2 파라미터

```
POST /api/pipeline/stage2  (JSON)
  run_label: string (필수)
  base_date: YYYYMMDD (선택, 스케줄 시작일 default=KST 당일)
```

---

## 원본 계획서 비교 결과

3.25계획.xls (수기 계획서) 대비 자동 생성 결과:

| 시트 | 원본 | 자동 | 차이 | 비고                                             |
| ---- | ---- | ---- | ---- | ------------------------------------------------ |
| 연선 | 101  | 103  | +2   | WIP 표기 정책(+3), 150SQ(-1 수동분할)            |
| B100 | 83   | 99   | +16  | WIP 표기(+13 의도), 나머지 데이터 차이           |
| A100 | 62   | 65   | +3   | WIP 표기 정책                                    |
| A120 | 40   | 40   | 0    | 완전 일치                                        |
| 간트 | 수기 | 자동 | -    | 설비/SQ/공정순서 일치, 배정 순서는 알고리즘 차이 |

---

## 환경 변수

| 변수                  | 기본값                         | 설명            |
| --------------------- | ------------------------------ | --------------- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000`        | 백엔드 API 주소 |
| `DATABASE_URL`        | `sqlite:///./kbi_scheduler.db` | DB 연결         |

---

## 라이선스

Internal use only (삼일PwC - KBI PoC)
