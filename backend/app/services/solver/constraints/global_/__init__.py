"""Global constraints — 모든 batch / run-level 적용.

해당 ConstraintConfig 분류:
  - 1-1 납기 우선 (tardiness)
  - 1-2 자재 입고 / 1-3 긴급수주 (현재 build_model 외부)
  - 6-1 캘린더 안전 / 6-2 금요일 / 6-3 결근 / 6-4 공휴일 (현재 build_model 외부)
"""
