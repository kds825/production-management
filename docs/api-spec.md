# API Specification

**Audience:** anyone integrating with the backend (frontend code,
external scrapers, the Excel exporter) or auditing the public surface.
**Source of truth:** the FastAPI app at `backend/app/main.py` — every
router included here is reachable in production. Use this document as a
**map**; for parameter shapes, request/response schemas, and example
payloads, generate the live OpenAPI snapshot via the recipe below.

---

## Regenerating `openapi.json`

There is no live server inside the docs build, so the OpenAPI spec is
not embedded in this repo. To produce a fresh snapshot:

```bash
cd backend && source venv/bin/activate
uvicorn app.main:app --port 8000 &
sleep 2
curl -s http://localhost:8000/openapi.json | jq . > docs/openapi-snapshot.json
kill %1
```

Review the diff before committing. If the diff isn't what you intended,
that's a public-surface change you didn't realise you shipped — fix the
route or update the spec deliberately.

You can also browse the live spec at `http://localhost:8000/docs`
(Swagger UI) or `http://localhost:8000/redoc` (Redoc) when uvicorn is
running.

---

## Route map (registered in `backend/app/main.py:53-62`)

Every router below is mounted under the `/api` prefix. The single
exception is `/metrics` (Prometheus scrape, mounted at root per
convention — `app/main.py:79`).

| Prefix                                    | Router file                       | Tag                  | Purpose                                                                                                                                                                |
| ----------------------------------------- | --------------------------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/health`                             | `app/main.py:65-68`               | `헬스체크`           | Liveness probe. Returns `{"status": "ok", "service": ...}`.                                                                                                            |
| `/api/equipment`                          | `routes/equipment.py`             | `설비`               | Read-only equipment master + detail.                                                                                                                                   |
| `/api/orders`                             | `routes/orders.py`                | `수주`               | Read-only sales order master + detail.                                                                                                                                 |
| `/api/schedules`                          | `routes/schedules/` (sub-package) | `스케줄`             | Schedule task CRUD + bulk update + cascade preview + revert. Split into `list.py`, `detail.py`, `bulk_update.py`, `cascade.py`, `revert.py` per Week 7 Task 7A.1.      |
| `/api/constraints`                        | `routes/constraints.py`           | `제약 조건`          | Constraint config CRUD, validation endpoint, change history, baseline snapshots (promote / reset / list / diff).                                                       |
| `/api/process-routes`, `/api/line-speeds` | `routes/process_routes.py`        | `공정 경로`          | Read-only process-route + line-speed master.                                                                                                                           |
| `/api/pipeline`                           | `routes/plan_pipeline.py`         | `파이프라인`         | Stage 1 (ERP upload → batches) and Stage 2 (solve) endpoints. Includes `/stage1/update` (Freeze & Rebuild) and `/stage1/{run_label}/ai-summary` (legacy LLM narrator). |
| `/api/master/{table_name}`                | `routes/master_data.py`           | `마스터데이터`       | Generic CRUD over master tables (per-table allow-list enforced inside the router).                                                                                     |
| `/api/audit`                              | `routes/audit.py`                 | `감사추적`, `SM재고` | `/audit/{run_label}` solver audit, `/audit/explain/{batch_id}` legacy LLM narration, `/audit/wip/...` SM-inventory CRUD.                                               |
| `/api/decisions`                          | `routes/decisions.py`             | `decisions`          | **Week 4** — `GET /{batch_id}/latest` returns the per-batch Decision Card payload (top-3 contributions + LLM summary + template-fallback flag).                        |
| `/api/change-sets`                        | `routes/change_sets.py`           | `change-sets`        | **Week 5** — `PATCH /{change_set_id}/reason` for manual-override reason chips, `GET /missing-reasons` for the dashboard badge counter.                                 |
| `/metrics`                                | `app/main.py:79-82`               | `관측성`             | Prometheus text-exposition scrape endpoint (no `/api` prefix per Prometheus convention).                                                                               |

---

## Notes per router

- **`schedules` is a sub-package, not a single file.** All five routes
  mount under `prefix="/schedules"` because the package's own
  `__init__.py` defines the parent `APIRouter` and includes each
  submodule's router with `prefix=""`. Don't try to import any
  submodule directly from `routes/schedules.py` — that file no longer
  exists post-Week-7.

- **`plan_pipeline` still owns the legacy LLM narrator** at
  `/pipeline/stage1/{run_label}/ai-summary`. The Week 4 Decision Card
  pathway (`routes/decisions.py`) is **separate**: it doesn't share
  prompts, providers, or the kiwipiepy filter with the legacy summary.
  See `docs/llm-prompt-inventory.md` for the prompt distinction.

- **`master_data` is intentionally generic.** Adding a new master
  table requires (a) the SQLAlchemy model, (b) a Pydantic schema, and
  (c) an entry in the router's allow-list — there is no auto-discovery.

- **Middlewares run in this order for every response** (`main.py:36-50`):
  1. `RunIdMiddleware` (outermost — added last) — sets the contextvar
     and stamps `X-Run-Id` on the response, including CORS preflight.
  2. `CORSMiddleware` (inner — added first) — handles preflight + adds
     `Access-Control-*` headers.

  This ordering is load-bearing — see `app/main.py:32-50` for the
  rationale and Week 2A.4 commit `9d3460b` for the swap that fixed it.
