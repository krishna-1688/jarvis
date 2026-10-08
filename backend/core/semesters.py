"""
core/semesters.py — VTOP semester IDs, worked out for whoever is using Jarvis.

VTOP semester IDs follow one pattern: campus prefix + academic year + term,
e.g. CH20262701 = Chennai, 2026-27, Fall (term 01); CH20252605 = Chennai,
2025-26, Winter (term 05). These used to be hardcoded for one student's
batch, so every other student's attendance/marks queries asked VTOP for
the wrong semesters. Now they're derived from the student's admission
year (from the registration number, e.g. 24BCE1234 -> 2024, or
profile.toml [college] admission_year) and today's date, so the current
semester also rolls over by itself each July (Fall) and January (Winter).

profile.toml [college] current_sem = N pins the semester if VIT's
calendar ever lands on a different month than this assumes.
"""

import os
import re
from datetime import date

CAMPUSES = {
    # prefix in semester IDs, VTOP host. Chennai is what this was built and
    # tested on; Vellore follows the same scheme but is untested here.
    "chennai": {"prefix": "CH", "host": "vtopcc.vit.ac.in"},
    "vellore": {"prefix": "VL", "host": "vtop.vit.ac.in"},
}
MAX_SEMESTERS = 8

_REG_NO_RE = re.compile(r"^(\d{2})[A-Z]{3}\d{4}$", re.IGNORECASE)


def _college() -> dict:
    from core import profile
    return profile.get().get("college", {})


def campus() -> dict:
    return CAMPUSES.get(str(_college().get("campus", "chennai")).lower(), CAMPUSES["chennai"])


def admission_year() -> int | None:
    year = int(_college().get("admission_year") or 0)
    if year:
        return year
    m = _REG_NO_RE.match((os.getenv("VTOP_USERNAME") or "").strip())
    return 2000 + int(m.group(1)) if m else None


def _academic_year(today: date) -> tuple[int, bool]:
    """(year the academic year started, is it the Fall term). Fall runs
    Jul-Dec, Winter Jan-Jun of the following calendar year."""
    return (today.year, True) if today.month >= 7 else (today.year - 1, False)


def sem_id(n: int, admit: int, prefix: str) -> str:
    start = admit + (n - 1) // 2
    return f"{prefix}{start}{(start + 1) % 100:02d}{'01' if n % 2 else '05'}"


def sem_label(n: int, admit: int) -> str:
    start = admit + (n - 1) // 2
    return f"Semester {n} ({'Fall' if n % 2 else 'Winter'} {start}-{(start + 1) % 100:02d})"


def current_number(today: date | None = None) -> int:
    pinned = int(_college().get("current_sem") or 0)
    if pinned:
        return max(1, min(MAX_SEMESTERS, pinned))
    admit = admission_year()
    if not admit:
        return 1
    start, fall = _academic_year(today or date.today())
    return max(1, min(MAX_SEMESTERS, 2 * (start - admit) + (1 if fall else 2)))


def compute(today: date | None = None) -> tuple[str, list, dict]:
    """(CURRENT_SEM, SEM_IDS most recent first then future, SEM_LABELS)."""
    admit = admission_year()
    prefix = campus()["prefix"]
    if not admit:
        # No VTOP login / registration number: VIT features are off anyway.
        admit, _ = _academic_year(today or date.today())
    cur = current_number(today)
    order = list(range(cur, 0, -1)) + list(range(cur + 1, MAX_SEMESTERS + 1))
    ids = [sem_id(n, admit, prefix) for n in order]
    labels = {sem_id(n, admit, prefix): sem_label(n, admit) for n in range(1, MAX_SEMESTERS + 1)}
    return sem_id(cur, admit, prefix), ids, labels


def number_of(sem: str) -> int | None:
    """Semester number for an ID produced by compute()."""
    for n in range(1, MAX_SEMESTERS + 1):
        if compute()[2].get(sem, "").startswith(f"Semester {n} "):
            return n
    return None


CURRENT_SEM, SEM_IDS, SEM_LABELS = compute()
