"""Constraint category modules — Phase 1 decomposition of model_builder.

각 카테고리 디렉토리는 ConstraintConfig 분류 (plan §5.1) 의 하나에 대응:
  global_/  : 전체 적용 (1-1 tardiness, 1-2 arrival, 6-* calendar 등)
  process/  : 공정별 (4-1 spec_change, 4-2 color_change, 9-* 등)
  product/  : 품목/규격별 (5-* sq_equipment, 10-* 등)
  inventory/: WIP/SM 재고 + 자재 (2-*, 3-1, 8-*)
  color/    : sheath_color (3-2, 3-3, 3-4)
  fault/    : 설비/불량 (7-*)

각 모듈은 `def add_<name>(ctx: BuildContext) -> None` 또는 explicit-args 형 pure
함수를 노출. ``model_builder.build_model`` 가 카테고리 순서대로 호출하여 모델을
구성한다.

Why pure functions, not Protocol/registry:
  사용자 메모리 ``feedback_constraint_arch_simplicity`` 참조. 38 클래스 +
  auto-discovery 는 PoC 단계에서 over-engineering. 명시적 import + 호출 순서
  가 디버깅/검증에 유리.
"""
