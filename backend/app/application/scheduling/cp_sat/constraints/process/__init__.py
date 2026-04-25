"""Process constraints — applicable_processes 가 비어있지 않은 row.

해당 ConstraintConfig 분류:
  - 4-1 spec_change_setup, 4-2 color_change, 4-3 drum_winding,
    4-4 welding, 4-5 taping_speed
  - 9-1 multicore_trigger, 9-2 gc_routing, 9-3 tfr_gv_bypass

build_model 내 sheath_color_hard / sheath_color_sequence / EDD_pair / transition
은 본 카테고리로 분류 (process_name 분기에 의존).
"""
