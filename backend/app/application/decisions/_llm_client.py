"""LLM HTTP 클라이언트 공용 헬퍼 — explain_batch 와 summarize_run 양쪽에서 사용.

Why 분리: 두 use-case 가 동일 OpenAI/Anthropic HTTP 호출 구조를 쓴다 (시스템
프롬프트 / 사용자 프롬프트만 다름). 호출 경로를 한 모듈에 모아 .env 적재 +
provider 분기 + 타임아웃을 단일 지점에서 관리한다.

직전 위치: `services/llm_explainer.py::_call_llm_sync` + 모듈 상단 .env 로딩
+ provider/모델 환경변수 (Phase 2 step 2/3 에서 분리, 동작 동일).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx

# .env 파일에서 환경변수 로드 (서버 프로세스에서도 동작하도록).
# Phase 2 분리 후에도 옛 위치(services/llm_explainer.py) 와 동일 우선순위로
# .env 가 한 번만 적재되도록 setdefault 사용.
_env_path = Path(__file__).resolve().parents[3] / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())


# Provider selection: "openai" | "anthropic" (default: "openai")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

# Anthropic (PwC GenAI Gateway 또는 직접 Anthropic API)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = os.getenv(
    "ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages"
)
ANTHROPIC_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")


# 기본 시스템 프롬프트 — explain_batch 가 사용. summarize_run 은 자체
# SUMMARY_SYSTEM_PROMPT 를 system_prompt 인자로 전달한다.
DEFAULT_SYSTEM_PROMPT = """당신은 전선 제조 공장의 생산계획 AI 어시스턴트입니다.
스케줄링 시스템이 내린 결정의 근거를 공장 관리자가 이해할 수 있는 한국어로 설명합니다.

규칙:
- 전문 용어는 공장에서 쓰는 표현 그대로 사용 (SQ, 틀단위, 연선, 시스 등)
- "~에 배치했습니다" 형식의 서술형 문장 사용
- 적용된 제약조건을 하나씩 나열하되, 이유를 함께 설명
- 대안이 있었다면 왜 기각했는지도 설명
- 납기 준수 여부를 명확히 언급
- 3~5문장으로 간결하게"""


def call_llm_sync(
    context: str,
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
) -> Optional[str]:
    """동기 LLM 호출 — httpx 동기 클라이언트 사용.

    system_prompt를 명시하지 않으면 기본 DEFAULT_SYSTEM_PROMPT를 사용한다.
    user_prompt를 명시하지 않으면 기본 스케줄링 설명 요청 문구를 사용한다.

    실패 시 None 반환 (예외를 호출부로 전파하지 않음 — fallback 경로 보장).
    """
    effective_system = (
        system_prompt if system_prompt is not None else DEFAULT_SYSTEM_PROMPT
    )
    effective_user = (
        user_prompt
        if user_prompt is not None
        else f"다음 스케줄링 결정의 근거를 공장 관리자에게 설명해주세요:\n\n{context}"
    )
    if LLM_PROVIDER == "openai" and OPENAI_API_KEY:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": OPENAI_MODEL,
                        "messages": [
                            {"role": "system", "content": effective_system},
                            {"role": "user", "content": effective_user},
                        ],
                        "max_completion_tokens": 500,
                        "temperature": 0.3,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
        except Exception:
            pass
    elif LLM_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    ANTHROPIC_API_URL,
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": ANTHROPIC_MODEL,
                        "max_tokens": 500,
                        "system": effective_system,
                        "messages": [{"role": "user", "content": effective_user}],
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data["content"][0]["text"]
        except Exception:
            pass
    return None
