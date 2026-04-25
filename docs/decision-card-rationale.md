# Decision Card — Why Inline, Not Popover

**Audience:** anyone touching `frontend/src/components/scheduler/DecisionCard.tsx` or
the gantt rows in `SchedulerView.tsx`.
**Status:** retrospective — captures the D6 design-review flip from Rev 1
(popover) to the shipped inline slide-down.
**See also:** `docs/plans/production-handoff-refactor.md` §D6 for the
original review notes.

---

Rev 1 of the Decision Card was a floating popover anchored above the gantt
row. The design review (D6) flipped it to an inline slide-down rendered
_beneath_ the row, in the same scroll context. Three reasons drove the flip:

1. **Comparison.** Operators routinely read a Decision Card for batch _N_
   while keeping batch _N±1_ in view to sanity-check why the solver picked
   one over the other. A floating popover always overlapped at least one
   neighbor, so the comparison the operator wanted was the comparison the
   popover obscured. Inline pushes the rows below batch _N_ down by the
   card's height — every other row stays visible and aligned.

2. **Print fidelity.** KBI shop-floor practice is to print the daily gantt
   and post it on the line. Floating overlays don't survive print: the
   browser print pipeline either drops them entirely or layers them on top
   of unrelated rows. Inline cards print as part of the page flow, exactly
   where they sat on screen.

3. **`is_manually_adjusted` affordance.** The yellow card variant
   (`var(--color-yellow-100)` background, `var(--color-yellow-600)` border)
   is the visual cue for an operator-adjusted batch. A popover couldn't
   carry that affordance without extra chrome — an outer ring, a corner
   badge, or a tab — all of which add UI weight. The inline card simply
   swaps its background token; the cue lives where the explanation lives.

The card is implemented in
`frontend/src/components/scheduler/DecisionCard.tsx` and toggled from
`frontend/src/components/scheduler/page-sections/SchedulerGantt.tsx` via
the `expandedBatchId` slice in `scheduleStore`.
