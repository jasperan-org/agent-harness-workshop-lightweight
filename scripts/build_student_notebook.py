#!/usr/bin/env python3
"""Validate the checked-in lightweight workshop notebooks.

The repository now keeps the five-TODO student notebook and its answer key as
source-controlled artifacts. This command replaces the old generator, which
referenced notebooks and docs that are no longer part of the lightweight path.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUDENT = ROOT / "notebook_student.ipynb"
COMPLETE = ROOT / "notebook_complete.ipynb"
REQUIRED = ("_scan_tables", "retrieve_knowledge", "hybrid_rrf_search_memories", "tool_run_sql", "agent_turn")


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        notebook = json.load(handle)
    if not isinstance(notebook.get("cells"), list):
        raise ValueError(f"{path.name}: missing cells list")
    return notebook


def sources(notebook: dict) -> list[str]:
    return ["".join(cell.get("source", [])) for cell in notebook["cells"]]


def main() -> int:
    student = sources(load(STUDENT))
    complete = sources(load(COMPLETE))
    stubs = [source for source in student if "NotImplementedError" in source]
    if len(stubs) != 5:
        raise ValueError(f"student notebook should contain exactly 5 TODO stubs, found {len(stubs)}")
    if any("NotImplementedError" in source for source in complete):
        raise ValueError("complete notebook still contains a TODO stub")
    joined = "\n".join(student + complete)
    missing = [name for name in REQUIRED if name not in joined]
    if missing:
        raise ValueError(f"required workshop symbols are missing: {', '.join(missing)}")
    print(f"student: {len(student)} cells, 5 TODO stubs")
    print(f"complete: {len(complete)} cells, no TODO stubs")
    print("notebook validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
