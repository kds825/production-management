"""Unit tests for solver/lex_min_time.py.

Tests use a tiny synthetic BuiltModel (3 groups, 1 equipment) — no DB,
no fixtures. Verifies the lex-min mechanism end-to-end:

  * scenario 12: all due dates feasible → T*=0, makespan minimized
  * scenario 13: one group past-due → T*>0, makespan minimized within T*
  * empty model: degenerate (no groups) → OPTIMAL / makespan=0
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from app.application.scheduling.cp_sat.lex_min_time import solve_lex_min_time
from app.application.scheduling.cp_sat.model_builder import BuiltModel


def _make_built(group_durations: list[tuple[str, int]], dues: dict[str, int]):
    """Build a tiny model: each group has dur, due_wmin given by dues[gk].

    Returns (built_model, eq_var_dict). dues missing key → no due (no tardiness).
    """
    model = cp_model.CpModel()
    horizon = 100_000

    start_vars: dict[str, cp_model.IntVar] = {}
    end_vars: dict[str, cp_model.IntVar] = {}
    equip_vars: dict[str, dict[str, cp_model.IntVar]] = {}
    tardiness_vars: dict[str, cp_model.IntVar] = {}
    itv_vars: dict[tuple[str, str], object] = {}

    for gk, dur in group_durations:
        s = model.new_int_var(0, horizon - dur, f"s_{gk}")
        e = model.new_int_var(dur, horizon, f"e_{gk}")
        model.add(e == s + dur)
        start_vars[gk] = s
        end_vars[gk] = e

        bv = model.new_bool_var(f"eq_{gk}_E1")
        model.add(bv == 1)
        equip_vars[gk] = {"E1": bv}
        itv = model.new_optional_interval_var(s, dur, e, bv, f"itv_{gk}_E1")
        itv_vars[(gk, "E1")] = itv

        if gk in dues:
            tard = model.new_int_var(0, 2 * horizon, f"t_{gk}")
            model.add_max_equality(tard, [e - dues[gk], model.new_constant(0)])
            tardiness_vars[gk] = tard

    # 단일 설비: no_overlap
    itvs = list(itv_vars.values())
    if len(itvs) >= 2:
        model.add_no_overlap(itvs)

    return BuiltModel(
        model=model,
        groups=[gk for gk, _ in group_durations],
        all_eq_codes=["E1"],
        start_vars=start_vars,
        end_vars=end_vars,
        equip_vars=equip_vars,
        tardiness_vars=tardiness_vars,
        dur_vars={gk: None for gk, _ in group_durations},
        itv_vars=itv_vars,
        idle_terms=[],
        transition_terms=[],
        sheath_end_terms=[],
        slack_terms=[],
        slack_terms_meta=[],
        edd_pair_terms=[],
        edd_mixed_pastdue_terms=[],
        warm_start_applied=0,
        warm_start_skipped=0,
    )


def test_lex_due_feasible_all_meet():
    """Scenario 12: 3 groups (dur 100/200/300), all due 1000 → T*=0."""
    built = _make_built(
        [("g1", 100), ("g2", 200), ("g3", 300)],
        dues={"g1": 1000, "g2": 1000, "g3": 1000},
    )
    result = solve_lex_min_time(
        built, time_limit_phase_a_sec=5, time_limit_phase_b_sec=5
    )
    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.t_star == 0, f"expected all due met, got T*={result.t_star}"
    assert result.all_due_met
    # 단일 설비 sequential placement: makespan = sum(dur) = 600
    assert result.makespan_min == 600, (
        f"expected makespan=600, got {result.makespan_min}"
    )


def test_lex_due_infeasible_minimizes_max_tard():
    """Scenario 13: dur 500 + due 100 → impossible at-time placement.

    Single equipment forces sequential. due=100 on a 500-dur group cannot be
    met. T* should be >0 (= dur - due = 400) and makespan = dur = 500.
    """
    built = _make_built(
        [("g1", 500)],
        dues={"g1": 100},
    )
    result = solve_lex_min_time(
        built, time_limit_phase_a_sec=5, time_limit_phase_b_sec=5
    )
    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.t_star > 0, f"expected past-due, got T*={result.t_star}"
    assert not result.all_due_met
    # tardiness = max(0, end - due) = max(0, 500 - 100) = 400
    assert result.t_star == 400, f"expected T*=400, got {result.t_star}"
    assert result.makespan_min == 500


def test_lex_no_groups_degenerate():
    """Empty model: no groups → T*=0, makespan=0."""
    built = _make_built([], dues={})
    result = solve_lex_min_time(
        built, time_limit_phase_a_sec=2, time_limit_phase_b_sec=2
    )
    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.t_star == 0
    assert result.makespan_min == 0


def test_lex_no_dues_returns_zero_tstar():
    """All groups have no due → T*=0 (max of empty tardiness_vars = 0)."""
    built = _make_built(
        [("g1", 100), ("g2", 200)],
        dues={},  # no tardiness_vars created
    )
    result = solve_lex_min_time(
        built, time_limit_phase_a_sec=5, time_limit_phase_b_sec=5
    )
    assert result.t_star == 0
    assert result.makespan_min == 300
