"""Product constraints — spec/product_group/sq_mm2 별 분기.

해당 ConstraintConfig 분류:
  - 5-1 sq_equipment, 5-2 stranding_type, 5-3 multicore_priority,
    5-4 bare_wire, 5-5 tfr_gv_insulation
  - 10-1, 10-2 conductor_material, 10-3 sheath_material, 10-4 voltage,
    10-5 four_core_calc

대부분 input_builder + greedy 영역에 살아있고 build_model 내 직접 항은 없음.
Phase 1 후순위 (Phase 4 의 input_builder 분할 시 함께 처리).
"""
