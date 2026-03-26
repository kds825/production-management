# KBI 생산계획 스케줄러

KBI 코스모링크 전선 공장의 생산계획을 시각화하고 관리하는 웹 도구입니다.

설비(Y축) x 날짜(X축) 간트 차트에서 드래그&드롭으로 작업을 배정하고, 제약조건(설비 적합성, 시간 겹침, 납기)을 실시간으로 검증합니다.

---

## 기술 스택

| 영역     | 기술                                           |
| -------- | ---------------------------------------------- |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS |
| 스케줄러 | @dnd-kit/core (커스텀 CSS Grid Gantt)          |
| 상태관리 | Zustand + Immer                                |
| Backend  | FastAPI (Python 3.11+)                         |
| DB       | In-memory (PoC)                                |

---

## 사전 준비

- **Node.js** 20 이상
- **Python** 3.11 이상
- **npm** 10 이상
- **Git**

---

## 설치 및 실행 (macOS / Linux)

### 1. 저장소 클론

```bash
git clone https://github.com/busyway1/KBI-Production-Management.git
cd KBI-Production-Management
```

### 2. Backend 설치 및 실행

```bash
cd backend

# 가상환경 생성 및 활성화
python3 -m venv venv
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt

# 서버 실행 (포트 8000)
uvicorn app.main:app --reload --port 8000
```

서버가 정상 기동되면:

- Swagger UI: http://localhost:8000/docs
- Health check: http://localhost:8000/api/health

### 3. Frontend 설치 및 실행

새 터미널을 열고:

```bash
cd frontend

# 의존성 설치
npm install

# 개발 서버 실행 (포트 3000)
npm run dev
```

브라우저에서 http://localhost:3000 접속.

---

## 설치 및 실행 (Windows)

### 1. 저장소 클론

```powershell
git clone https://github.com/busyway1/KBI-Production-Management.git
cd KBI-Production-Management
```

### 2. Backend 설치 및 실행

```powershell
cd backend

# 가상환경 생성
python -m venv venv

# 가상환경 활성화 (PowerShell)
.\venv\Scripts\Activate.ps1

# 만약 실행 정책 오류가 나면:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# cmd를 사용하는 경우:
# venv\Scripts\activate.bat

# 의존성 설치
pip install -r requirements.txt

# 서버 실행
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend 설치 및 실행

새 터미널(PowerShell 또는 cmd)을 열고:

```powershell
cd frontend

# 의존성 설치
npm install

# 개발 서버 실행
npm run dev
```

브라우저에서 http://localhost:3000 접속.

---

## 프로젝트 구조

```
KBI-Production-Management/
├── backend/                    # FastAPI 백엔드
│   ├── app/
│   │   ├── domain/             # 엔티티, 제약조건 규칙
│   │   ├── application/        # 유스케이스
│   │   ├── infrastructure/     # 데이터 저장소
│   │   └── presentation/       # API 라우트, 스키마
│   ├── tests/                  # pytest 테스트
│   ├── seed_data.py            # KBI 설비/수주/선속 시드 데이터
│   └── requirements.txt
│
├── frontend/                   # Next.js 프론트엔드
│   └── src/
│       ├── app/                # 라우트 페이지
│       ├── features/
│       │   ├── scheduler/      # 간트 스케줄러
│       │   ├── plan-register/  # 생산계획 등록
│       │   └── scheduling-review/ # 스케줄링 검토
│       └── shared/             # 공통 컴포넌트
│
├── Documents/                  # 참고 문서 (제안서, 흐름도 등)
├── Preparation/                # PoC 사전 준비 문서
└── README.md
```

---

## 주요 기능

### 간트 스케줄러 (`/scheduler`)

- 16대 설비 x 날짜 간트 차트
- 미배정 수주를 드래그하여 설비에 배정
- 작업 블록 드래그 이동 / 리사이즈
- 겹침 방지 (cascade push: 같은 설비에서 블록이 겹치면 뒤 블록이 자동으로 밀림)
- 제약조건 실시간 검증 (설비 적합성, 납기 초과)
- 수정하기/저장하기 모드 (버전 관리)
- 공정별 / 고압-저압별 필터
- 줌 컨트롤 (주/일/시간)
- 우클릭 컨텍스트 메뉴 (작업 추가/수정/삭제)

### 생산계획 등록 (`/plan-register`)

- 수주 데이터 업로드 및 배치 그룹핑
- 재공 수량 관리

### 스케줄링 검토 (`/scheduling-review`)

- 배치별 소요시간 계산
- AI 인사이트 카드
- 공정 최적화 제안

---

## API 엔드포인트

| Method | Path                        | 설명              |
| ------ | --------------------------- | ----------------- |
| GET    | `/api/health`               | 서버 상태 확인    |
| GET    | `/api/equipment`            | 설비 목록 (16대)  |
| GET    | `/api/orders`               | 수주 목록         |
| GET    | `/api/schedules/tasks`      | 스케줄 작업 목록  |
| POST   | `/api/schedules/tasks`      | 작업 추가         |
| PUT    | `/api/schedules/tasks/{id}` | 작업 수정         |
| DELETE | `/api/schedules/tasks/{id}` | 작업 삭제         |
| POST   | `/api/constraints/validate` | 제약조건 검증     |
| GET    | `/api/process-routes`       | 공정 경로 (6가지) |
| GET    | `/api/line-speeds`          | 규격별 선속       |
| POST   | `/api/schedules/versions`   | 버전 저장         |
| GET    | `/api/schedules/versions`   | 버전 목록         |

---

## 테스트

### Backend

```bash
cd backend
source venv/bin/activate   # Windows: .\venv\Scripts\Activate.ps1
python -m pytest tests/ -v
```

### Frontend

```bash
cd frontend
npx tsc --noEmit       # 타입 체크
npm run build          # 프로덕션 빌드
```

---

## 환경 변수 (선택)

| 변수                  | 기본값                         | 설명                  |
| --------------------- | ------------------------------ | --------------------- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000`        | 백엔드 API 주소       |
| `DATABASE_URL`        | `sqlite:///./kbi_scheduler.db` | DB 연결 (PoC: SQLite) |

---

## 라이선스

Internal use only (삼일PwC - KBI PoC)
