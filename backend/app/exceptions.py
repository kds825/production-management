"""애플리케이션 전역 예외 클래스."""

from __future__ import annotations


class SchedulerOverlapError(Exception):
    """겹침 재시도 2회 실패 — 스케줄 저장 거부 신호.

    라우트 레이어가 이 예외를 잡아 `overlap_alert=True` HTTP 응답을 반환하고,
    기존 DB 스케줄은 수정하지 않는다.
    """

    def __init__(
        self,
        message: str,
        *,
        run_label: str,
        violations: list[dict],
        attempts: int,
    ):
        super().__init__(message)
        self.run_label = run_label
        self.violations = violations
        self.attempts = attempts
