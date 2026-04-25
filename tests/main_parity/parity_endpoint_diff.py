"""실 서버 두 인스턴스 응답 비교 — refactoring(8000) vs main(8001).

본 라운드 (docs/next-session-prompt.md §3) 의 main-parity gate. 외형 동치를
보장하기 위해 GET 28개 endpoint + 일부 POST 를 양 서버에서 호출하고 normalize 후
deep-diff 한다.

비교 정책:
  1. status_code 정확히 일치.
  2. 응답 body 는 normalize() 후 deep_diff:
     - 시간 필드 (created_at, started_at, finished_at, generated_at, _at) 마스킹.
     - run_id (uuid4 패턴) 마스킹.
     - run_label 은 매핑 dict 로 양측 동치 처리 (latest run_label 이 양 서버에서
       동일 Supabase 를 보고 있으므로 보통 같지만, 안전망).
  3. EXPECTED_DRIFT 화이트리스트 외 모든 diff = 회귀.

사용:
  cd /Users/jaewookim/Desktop/Project/KBI_PoC
  /Users/jaewookim/Desktop/Project/KBI_PoC/backend/venv/bin/python \
      tests/main_parity/parity_endpoint_diff.py \
      --refactoring http://127.0.0.1:8000 \
      --main http://127.0.0.1:8001 \
      --report /tmp/parity_diff_report.md

Why GET-only 우선:
  Supabase 단일 DB 를 양 서버가 공유하므로 destructive POST (stage1/stage2,
  schedule revert) 는 두 번 실행 시 두 번째가 다른 state 를 본다. POST 비교는
  Phase 1+ 에서 ephemeral docker DB (kbi_postgres:5432) 로만 가능 — 본 baseline
  단계에서는 GET 만 비교한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib import error as urlerror
from urllib import request as urlreq

# ── 의도적 drift 화이트리스트 ──────────────────────────────────────
# refactoring 에는 있고 main 에는 없는 신규 endpoint. 본 라운드 종료 시점에도
# 이 목록은 유지되어야 한다 (제거되면 신규 기능 회귀).
EXPECTED_REFAC_ONLY = {
    "/api/change-sets/missing-reasons",
    "/api/change-sets/{change_set_id}/reason",
    "/api/constraints/baselines",
    "/api/constraints/promote-baseline",
    "/api/constraints/reset-to-baseline",
    "/api/constraints/versions/{a}/diff/{b}",
    "/api/decisions/{batch_id}/latest",
}

# 의도적 status / body drift. 형식: ("METHOD /path", reason).
EXPECTED_DRIFT = {
    "POST /api/pipeline/stage1/urgent": "refactoring=410(retired ca00e1b), main=500(FK bug)",
}

# LLM 비결정론 endpoint — Anthropic API 가 같은 입력에 다른 prose 를 생성.
# Plan §3.2 가 ai-summary 에 "fuzzy match OK" 라고 명시. 본 endpoint 들은
# 텍스트 평등 비교 대신 response shape (top-level keys + 배열 길이) 만 비교.
LLM_NONDETERMINISTIC = {
    "/api/pipeline/stage1/{run_label}/ai-summary",
    "/api/audit/explain/{batch_id}",
}

# Shape-only — DB-state drift / binary metadata noise. 같은 Supabase 를
# 양 서버가 공유하므로 list 길이가 GET 사이에 바뀔 수 있고, xlsx 파일은
# 생성 시각 metadata 가 byte 단위로 들어감.
STATEFUL_OR_BINARY = {
    "/api/pipeline/runs",  # Supabase 동시 변경 시 length 변동
    "/api/pipeline/stage1/{run_label}/export",  # xlsx 생성 timestamp metadata
    "/api/pipeline/wip-template",  # xlsx 생성 timestamp metadata
}


def shape_only(value, depth: int = 0, *, hide_list_len: bool = False):
    """LLM / stateful endpoint 비교용 — 값 대신 type + (선택적) keys 만 추출.

    hide_list_len=True 면 list 길이도 무시 (DB drift 대비). False (기본) 면
    list 의 element type 만 비교 (LLM 비결정론 — 길이는 같아야 함).
    """
    if depth > 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            k: shape_only(v, depth + 1, hide_list_len=hide_list_len)
            for k, v in sorted(value.items())
        }
    if isinstance(value, list):
        if hide_list_len:
            # length 차이도 무시 — element type 만 비교 (첫 element 만 sample).
            return (
                ["<list>", shape_only(value[0], depth + 1, hide_list_len=True)]
                if value
                else ["<empty>"]
            )
        return [f"<list len={len(value)}>"]
    if isinstance(value, (int, float)) and hide_list_len:
        # byte_len 같은 metadata 숫자 무시 (xlsx timestamp)
        return type(value).__name__
    return type(value).__name__


# 시간/uuid 패턴 — normalize() 가 마스킹.
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ISO_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?"
)
# run_label 형식: YYYYMMDD_HHMMSS (Stage1 이 부여)
_RUN_LABEL_RE = re.compile(r"\b\d{8}_\d{6}\b")
# 시간 의미를 가지는 키 이름 (값 자체 마스킹)
_TIME_KEY_HINTS = (
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    "completed_at",
    "generated_at",
    "_at",
    "timestamp",
    "datetime",
)


@dataclass
class EndpointResult:
    method: str
    path: str
    refac_status: int | None
    main_status: int | None
    diff: list[str] = field(default_factory=list)
    error: str | None = None
    expected: bool = False

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class Report:
    results: list[EndpointResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def regressions(self) -> list[EndpointResult]:
        return [r for r in self.results if (r.diff or r.error) and not r.expected]

    @property
    def expected_drifts(self) -> list[EndpointResult]:
        return [r for r in self.results if r.expected]

    @property
    def identical(self) -> list[EndpointResult]:
        return [r for r in self.results if not r.diff and not r.error]


def http_get(base: str, path: str, *, timeout: int = 30) -> tuple[int, Any, str | None]:
    """Returns (status, body_or_text, error)."""
    url = f"{base.rstrip('/')}{path}"
    try:
        req = urlreq.Request(url, method="GET")
        with urlreq.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("content-type", "")
            status = resp.status
    except urlerror.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        ctype = e.headers.get("content-type", "") if e.headers else ""
        status = e.code
    except Exception as e:
        return (0, None, f"{type(e).__name__}: {e}")
    if "json" in ctype.lower():
        try:
            return (status, json.loads(raw.decode("utf-8")), None)
        except Exception as e:
            return (status, raw.decode("utf-8", errors="replace"), f"json_decode: {e}")
    # 비-JSON (xlsx 템플릿 등) 은 길이 + content-type 만 비교
    return (
        status,
        {"_non_json": True, "byte_len": len(raw), "content_type": ctype},
        None,
    )


def normalize(value: Any) -> Any:
    """시간/uuid/run_label 마스킹 + dict 정렬."""
    if isinstance(value, dict):
        out = {}
        for k, v in sorted(value.items()):
            if any(hint in k.lower() for hint in _TIME_KEY_HINTS):
                out[k] = "<TS>"
            else:
                out[k] = normalize(v)
        return out
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, str):
        v = _UUID_RE.sub("<UUID>", value)
        v = _ISO_TS_RE.sub("<TS>", v)
        v = _RUN_LABEL_RE.sub("<RUN_LABEL>", v)
        return v
    return value


def deep_diff(a: Any, b: Any, path: str = "$") -> list[str]:
    """간이 deep-diff. 길이 차이를 우선 보고, 그 후 키별 diff."""
    if type(a) is not type(b):
        return [f"{path}: type {type(a).__name__} vs {type(b).__name__}"]
    if isinstance(a, dict):
        diffs = []
        keys = set(a.keys()) | set(b.keys())
        for k in sorted(keys):
            if k not in a:
                diffs.append(f"{path}.{k}: missing on REFAC")
            elif k not in b:
                diffs.append(f"{path}.{k}: missing on MAIN")
            else:
                diffs.extend(deep_diff(a[k], b[k], f"{path}.{k}"))
        return diffs
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: list len {len(a)} vs {len(b)}"]
        diffs = []
        # length 큰 list 는 첫 5개 element 만 비교 (성능)
        sample = min(len(a), 5) if len(a) > 5 else len(a)
        for i in range(sample):
            diffs.extend(deep_diff(a[i], b[i], f"{path}[{i}]"))
        return diffs
    if a != b:
        return [f"{path}: {a!r} != {b!r}"]
    return []


def discover_path_params(refac_base: str) -> dict[str, str]:
    """Path 템플릿에 들어갈 실제 값을 추출.

    /api/pipeline/runs → 가장 최근 run_label
    그 run_label 의 첫 batch → batch_id
    """
    params: dict[str, str] = {}
    status, body, _ = http_get(refac_base, "/api/pipeline/runs")
    if status == 200 and isinstance(body, list) and body:
        run = body[0]
        rl = run.get("run_label") or run.get("runLabel")
        if rl:
            params["run_label"] = rl
    if "run_label" in params:
        status, body, _ = http_get(
            refac_base, f"/api/pipeline/stage1/{params['run_label']}/batches"
        )
        if status == 200 and isinstance(body, list) and body:
            batch_id = body[0].get("batch_id") or body[0].get("id")
            if batch_id is not None:
                params["batch_id"] = str(batch_id)
            wip_id = body[0].get("wip_id")
            if wip_id:
                params["wip_id"] = str(wip_id)
    # constraints — 응답 형식: {constraints:[...], total, enabled}
    status, body, _ = http_get(refac_base, "/api/constraints")
    rows = None
    if status == 200:
        if isinstance(body, list):
            rows = body
        elif isinstance(body, dict):
            rows = body.get("constraints") or body.get("items")
    if rows:
        cid = rows[0].get("constraint_id") or rows[0].get("id")
        if cid is not None:
            params["constraint_id"] = str(cid)
    # schedules versions
    status, body, _ = http_get(refac_base, "/api/schedules/versions")
    if status == 200 and isinstance(body, list) and body:
        vid = body[0].get("id") or body[0].get("version_id")
        if vid is not None:
            params["version_id"] = str(vid)
    return params


# 본 라운드에서 비교 대상으로 잡는 GET endpoint. (param 자리는 discover 결과로 채움)
GET_ENDPOINTS: list[tuple[str, dict[str, str]]] = [
    ("/api/health", {}),
    ("/api/pipeline/runs", {}),
    ("/api/pipeline/runs/{run_label}", {"run_label": "run_label"}),
    ("/api/pipeline/stage1/{run_label}/batches", {"run_label": "run_label"}),
    ("/api/pipeline/stage1/{run_label}/wip-inventory", {"run_label": "run_label"}),
    ("/api/pipeline/stage1/{run_label}/outsourced", {"run_label": "run_label"}),
    ("/api/pipeline/stage1/{run_label}/ai-summary", {"run_label": "run_label"}),
    ("/api/pipeline/stage1/{run_label}/export", {"run_label": "run_label"}),
    ("/api/pipeline/batch/{batch_id}", {"batch_id": "batch_id"}),
    ("/api/pipeline/batch/{batch_id}/status", {"batch_id": "batch_id"}),
    ("/api/pipeline/batch-status-summary", {}),
    ("/api/pipeline/batch-group-snapshots", {}),
    ("/api/pipeline/wip-template", {}),
    ("/api/schedules/tasks", {}),
    ("/api/schedules/versions", {}),
    ("/api/schedules/versions/{version_id}", {"version_id": "version_id"}),
    ("/api/constraints", {}),
    ("/api/constraints/{constraint_id}", {"constraint_id": "constraint_id"}),
    ("/api/constraints/{constraint_id}/history", {"constraint_id": "constraint_id"}),
    ("/api/constraints/drift-status", {}),
    ("/api/equipment", {}),
    ("/api/orders", {}),
    ("/api/line-speeds", {}),
    ("/api/process-routes", {}),
    ("/api/audit/{run_label}", {"run_label": "run_label"}),
    ("/api/audit/wip/summary/{run_label}", {"run_label": "run_label"}),
    ("/api/audit/wip/shortage-batches", {}),
    ("/api/audit/explain/{batch_id}", {"batch_id": "batch_id"}),
]


def render_path(
    template: str, params: dict[str, str], values: dict[str, str]
) -> str | None:
    """params 매핑 ({"run_label": "run_label"}) 을 통해 values 에서 실제 값을 추출."""
    out = template
    for placeholder, key in params.items():
        v = values.get(key)
        if v is None:
            return None
        out = out.replace("{" + placeholder + "}", v)
    return out


def run_diff(refac_base: str, main_base: str) -> Report:
    print(f"\n=== Discovering path params from REFAC ({refac_base}) ===", flush=True)
    values = discover_path_params(refac_base)
    print(f"  resolved: {values}", flush=True)

    report = Report()
    for tmpl, param_map in GET_ENDPOINTS:
        path = render_path(tmpl, param_map, values)
        if path is None:
            print(f"  SKIP {tmpl}: no fixture for params {param_map}", flush=True)
            continue
        rs, rb, re_ = http_get(refac_base, path)
        ms, mb, me = http_get(main_base, path)
        result = EndpointResult(
            method="GET", path=tmpl, refac_status=rs, main_status=ms
        )

        if re_ or me:
            result.error = f"refac:{re_} main:{me}"
        elif rs != ms:
            result.diff = [f"status_code: refac={rs} main={ms}"]
        elif tmpl in STATEFUL_OR_BINARY:
            # DB drift / 바이너리 metadata — list 길이/숫자 무시한 shape.
            result.diff = deep_diff(
                shape_only(rb, hide_list_len=True),
                shape_only(mb, hide_list_len=True),
            )
        elif tmpl in LLM_NONDETERMINISTIC:
            # LLM 비결정론 — shape (top-level keys + 배열 길이) 만 비교.
            result.diff = deep_diff(shape_only(rb), shape_only(mb))
        else:
            result.diff = deep_diff(normalize(rb), normalize(mb))

        if result.label in EXPECTED_DRIFT:
            result.expected = True

        report.results.append(result)
        marker = (
            "✓"
            if not result.diff and not result.error
            else ("⚠ EXPECTED" if result.expected else "✗ DIFF")
        )
        print(f"  [{marker}] {result.label}  refac={rs} main={ms}", flush=True)
    return report


def write_report(report: Report, path: str) -> None:
    lines = [
        "# main-parity diff report",
        "",
        f"- total endpoints compared: {report.total}",
        f"- identical: {len(report.identical)}",
        f"- expected drift: {len(report.expected_drifts)}",
        f"- **regressions: {len(report.regressions)}**",
        "",
    ]
    if report.regressions:
        lines.append("## Regressions (action required)")
        for r in report.regressions:
            lines.append(f"### {r.label}")
            lines.append(
                f"- refac status: {r.refac_status}, main status: {r.main_status}"
            )
            if r.error:
                lines.append(f"- error: {r.error}")
            for d in r.diff[:30]:
                lines.append(f"  - `{d}`")
            if len(r.diff) > 30:
                lines.append(f"  - ... ({len(r.diff) - 30} more)")
            lines.append("")
    lines.append("## Identical")
    for r in report.identical:
        lines.append(f"- {r.label}  ({r.refac_status})")
    lines.append("")
    if report.expected_drifts:
        lines.append("## Expected drift")
        for r in report.expected_drifts:
            reason = EXPECTED_DRIFT.get(r.label, "")
            lines.append(f"- {r.label} — {reason}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nReport written → {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refactoring", default="http://127.0.0.1:8000")
    ap.add_argument("--main", default="http://127.0.0.1:8001")
    ap.add_argument("--report", default="/tmp/parity_diff_report.md")
    args = ap.parse_args()

    report = run_diff(args.refactoring, args.main)
    write_report(report, args.report)

    print(
        f"\nSummary: identical={len(report.identical)}/{report.total}, "
        f"expected={len(report.expected_drifts)}, "
        f"regressions={len(report.regressions)}"
    )
    return 0 if not report.regressions else 1


if __name__ == "__main__":
    sys.exit(main())
