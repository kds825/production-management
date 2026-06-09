# KBI Production Management - 배포 및 환경 설정 변경 기록

## 배포 환경 (2026-06-10)

| 구성 | 서비스 | URL |
|------|--------|-----|
| 프론트엔드 | Vercel | https://production-management-gules.vercel.app |
| 백엔드 | Render (Free) | https://kbi-backend.onrender.com |
| DB | Supabase (Seoul) | Session Pooler (포트 5432) |
| GitHub | kds825/production-management | 브랜치: `dev_daesung` |

## 환경변수

### 백엔드 (Render)

| Key | 설명 |
|-----|------|
| `DATABASE_URL` | Supabase Session Pooler 연결 문자열 (`postgresql+psycopg://...@...pooler.supabase.com:5432/postgres`) |
| `LLM_PROVIDER` | `openai` (PwC 게이트웨이 OpenAI 호환) |
| `OPENAI_API_KEY` | PwC GenAI SharedService 게이트웨이 키 |
| `OPENAI_API_BASE` | `https://genai-sharedservice-americas.pwcinternal.com/v1` |
| `LLM_MODEL` | `bedrock.anthropic.claude-sonnet-4-5` |
| `FEATURE_FLAG_CASCADE_V2` | `on` |

### 프론트엔드 (Vercel)

| Key | 설명 |
|-----|------|
| `NEXT_PUBLIC_API_URL` | `https://kbi-backend.onrender.com/api` |

## 코드 변경 사항

### 1. 환경 설정 파일 생성
- `.env` (루트) - 프론트엔드용 환경변수
- `backend/.env` - 백엔드용 환경변수
- 둘 다 `.gitignore`에 포함

### 2. DB 드라이버 전환: psycopg2 -> psycopg v3
- 원인: 한글 경로에서 psycopg2-binary `UnicodeDecodeError`
- `backend/requirements.txt` - `psycopg[binary]>=3.1` 추가
- `backend/app/infrastructure/database.py` - Session Pooler(포트 5432) 사용

### 3. Supabase 신규 프로젝트 연결
- Alembic 마이그레이션 19개 적용, 시드 데이터 271행 삽입

### 4. LLM 프로바이더: Anthropic SDK -> PwC 게이트웨이 (OpenAI 호환)
- `backend/app/infrastructure/llm/anthropic_provider.py` - OpenAI SDK로 변경
- `backend/app/infrastructure/llm/__init__.py` - `get_provider("openai")` 지원

### 5. kiwipiepy 한글 경로 문제
- `backend/app/application/decisions/narrator.py` - lazy loading으로 변경

### 6. ERP 파서 .xlsx 지원
- `backend/app/infrastructure/parsers/erp_parser.py` - openpyxl 우선, xlrd 폴백

### 7. 자동 배열 오버플로우 버그
- `backend/app/application/scheduling/greedy/_assign_group.py` - `datetime.max` 필터 추가

### 8. WIP FK 위반 수정
- `backend/app/application/ingest/pipeline_orchestrator.py` - FK NULL 화 추가
- `backend/app/presentation/routes/plan_pipeline_stage1.py` - subquery로 FK cycle 해제

### 9. CORS 설정
- `backend/app/config.py` - `CORS_ORIGINS = ["*"]` (시연용)

### 10. 프론트엔드 localhost 하드코딩 제거
- src/ 내 11개 파일 - 환경변수 사용으로 변경

### 11. Render 배포 설정
- `backend/.python-version` - Python 3.13.2 고정

## 시연 전 체크리스트

1. https://kbi-backend.onrender.com/api/health 접속하여 서버 깨우기 (30초~1분)
2. https://production-management-gules.vercel.app 접속
3. ERP 파일 업로드 -> 작업지시서 생성 -> 자동 배열

## 제한사항

- PwC 게이트웨이는 내부망에서만 접근 가능 (LLM 기능 제한)
- Render 무료: 15분 미사용 시 슬립
- CORS `*` 설정 - 운영 환경에서는 특정 도메인으로 제한 필요
