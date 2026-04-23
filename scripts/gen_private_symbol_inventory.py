#!/usr/bin/env python3
"""Generate docs/private-symbol-inventory.md.

Purpose:
    Authoritative list of every ``_private`` symbol that tests (and non-test
    code) currently import from the three modules scheduled for extraction in
    Weeks 2-3 of the Production Handoff Refactor:
        - app.services.cp_sat_optimizer
        - app.services.schedule_optimizer
        - app.services.batch_grouping

Why an AST parser and not grep?
    The grep one-liner in the plan misses multi-line imports such as::

        from app.services.cp_sat_optimizer import (
            _foo,
            _bar,
        )

    ``ast.ImportFrom`` handles single-line, multi-line, and aliased forms
    uniformly and reliably. For aliased re-exports (``import _foo as _bar``)
    we record ``alias.name`` (the SOURCE name), since the invariant is about
    what must remain importable from the source module.

D7-C invariant (Week 2/3):
    No source file may be ``rm``'d until every symbol listed in the generated
    doc is still importable from its original dotted path (either directly or
    via a re-export shell at that path).

Regenerate:
    python3 scripts/gen_private_symbol_inventory.py > docs/private-symbol-inventory.md

The script is intentionally deterministic (sorted, fixed module order, final
newline) so repeated runs produce zero diff — that property is part of the
verification step.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Run from repo root. Paths in the generated doc stay repo-relative — makes
# them greppable as-is from the project root.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Fixed module order: follows the Week 2/3 extraction order in the plan.
TARGET_MODULES: tuple[str, ...] = (
    "app.services.cp_sat_optimizer",
    "app.services.schedule_optimizer",
    "app.services.batch_grouping",
)

TESTS_DIR = REPO_ROOT / "backend" / "tests"
APP_DIR = REPO_ROOT / "backend" / "app"


def _parse_private_imports(
    py_files: list[Path],
) -> tuple[dict[str, set[str]], dict[str, dict[str, set[Path]]]]:
    """Parse a list of .py files; return (symbols_by_module, callers_by_symbol).

    - symbols_by_module: module -> {private_name, ...}
    - callers_by_symbol: module -> private_name -> {file paths that import it}

    Fail-fast on SyntaxError: a skipped file could mean a missed symbol, which
    would let a later ``rm`` silently break a test. Exit 2.
    """
    symbols: dict[str, set[str]] = {m: set() for m in TARGET_MODULES}
    callers: dict[str, dict[str, set[Path]]] = {m: {} for m in TARGET_MODULES}

    for path in sorted(py_files):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            print(
                f"error: SyntaxError parsing {path}: {exc}",
                file=sys.stderr,
            )
            sys.exit(2)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module not in TARGET_MODULES:
                continue
            for alias in node.names:
                # `alias.name` is the name in the source module; `alias.asname`
                # is the local binding. For the D7-C invariant we care about
                # the source-side name.
                name = alias.name
                if not name.startswith("_"):
                    continue
                symbols[node.module].add(name)
                callers[node.module].setdefault(name, set()).add(path)

    return symbols, callers


def _collect_py_files(root: Path, exclude: set[Path] | None = None) -> list[Path]:
    exclude = exclude or set()
    exclude_resolved = {p.resolve() for p in exclude}
    out: list[Path] = []
    for p in root.rglob("*.py"):
        if p.resolve() in exclude_resolved:
            continue
        # Skip compiled artifacts / venv-ish paths just in case.
        parts = set(p.parts)
        if "__pycache__" in parts or "venv" in parts or ".venv" in parts:
            continue
        out.append(p)
    return out


def _fmt_symbols_section(module: str, names: set[str]) -> list[str]:
    short = module.rsplit(".", 1)[-1]
    lines = [f"## {short} ({len(names)} symbols)", ""]
    if not names:
        lines.append("_No private symbols currently imported by tests._")
        lines.append("")
        return lines
    for name in sorted(names):
        lines.append(f"- `{name}`")
    lines.append("")
    return lines


def _fmt_nontest_section(
    nontest_symbols: dict[str, set[str]],
    nontest_callers: dict[str, dict[str, set[Path]]],
) -> list[str]:
    total = sum(len(s) for s in nontest_symbols.values())
    lines = [f"## Also imported by non-test code ({total} symbols)", ""]
    if total == 0:
        lines.append(
            "_No private symbols from the three target modules are imported "
            "by non-test code under `backend/app/`. The re-export surface is "
            "defined entirely by the test suite._"
        )
        lines.append("")
        return lines

    lines.append("| Module | Symbol | Importer |")
    lines.append("| --- | --- | --- |")
    for module in TARGET_MODULES:
        short = module.rsplit(".", 1)[-1]
        for name in sorted(nontest_symbols.get(module, set())):
            importers = sorted(
                str(p.relative_to(REPO_ROOT)) for p in nontest_callers[module][name]
            )
            for imp in importers:
                lines.append(f"| `{short}` | `{name}` | `{imp}` |")
    lines.append("")
    return lines


def main() -> int:
    # --- Test-side inventory (the primary deliverable) ---
    test_files = _collect_py_files(TESTS_DIR)
    test_symbols, _test_callers = _parse_private_imports(test_files)

    # --- Non-test-side inventory (secondary, for shell completeness) ---
    # Exclude the three target source files themselves — a module importing
    # its own private names doesn't imply an external caller.
    self_files = {REPO_ROOT / "backend" / m.replace(".", "/") for m in TARGET_MODULES}
    self_files_py = {p.with_suffix(".py") for p in self_files}
    # Also exclude the tests tree from the app walk (it isn't under app/ in
    # this repo, but belt-and-suspenders).
    nontest_files = _collect_py_files(APP_DIR, exclude=self_files_py)
    nontest_symbols, nontest_callers = _parse_private_imports(nontest_files)

    # --- Emit doc ---
    out: list[str] = []
    out.append("# Private-symbol inventory (Weeks 2/3 re-export surface)")
    out.append("")
    out.append(
        "Authoritative list of every `_private` symbol currently imported "
        "from `app.services.cp_sat_optimizer`, `app.services.schedule_optimizer`, "
        "and `app.services.batch_grouping` — the three modules scheduled for "
        "extraction in Weeks 2-3 of the Production Handoff Refactor."
    )
    out.append("")
    out.append(
        "**D7-C invariant.** No source file on this list may be `rm`'d until "
        "every symbol below remains importable from its original dotted path, "
        "either directly or via a re-export shell at that path. Violating "
        "this invariant silently breaks the test suite mid-refactor."
    )
    out.append("")
    out.append(
        "**Generator.** `scripts/gen_private_symbol_inventory.py` (AST-based; "
        "handles multi-line imports the grep one-liner misses). For aliased "
        "re-exports (`from X import _foo as _bar`) the source-side name "
        "`_foo` is recorded."
    )
    out.append("")
    out.append(
        "**Regenerate.** `python3 scripts/gen_private_symbol_inventory.py "
        "> docs/private-symbol-inventory.md` — run before Week 2 kickoff and "
        "again after Weeks 2/3 to confirm no new private-import surface "
        "emerged. Output is deterministic; re-running on an unchanged tree "
        "yields no diff."
    )
    out.append("")

    total_test_syms = 0
    for module in TARGET_MODULES:
        names = test_symbols.get(module, set())
        total_test_syms += len(names)
        out.extend(_fmt_symbols_section(module, names))

    out.extend(_fmt_nontest_section(nontest_symbols, nontest_callers))

    # Footer — NOT an H2, to keep exactly 4 `## ` headings (3 source modules
    # + 1 non-test section), which is the documented verification invariant.
    total_nontest_syms = sum(len(s) for s in nontest_symbols.values())
    out.append("---")
    out.append("")
    out.append(
        f"**Totals.** Test-side private imports: **{total_test_syms}** "
        f"across {len(TARGET_MODULES)} modules. Non-test-side private "
        f"imports: **{total_nontest_syms}**. Distinct (module, symbol) "
        f"pairs that must survive Weeks 2/3: "
        f"**{total_test_syms + total_nontest_syms}**."
    )
    out.append("")

    # Strip trailing whitespace per line, ensure exactly one final newline.
    rendered = "\n".join(line.rstrip() for line in out).rstrip() + "\n"
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
