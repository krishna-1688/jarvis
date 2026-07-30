"""
core/course_resolver.py — fuzzy course-name resolution (H.2).

Turns loose spoken course references ("daa", "the daa lab", "compiler")
into the actual course_code/course_name VTOP uses, by matching against
core.memory's course_aliases table — auto-populated from every
timetable/attendance sync (see core.memory.sync_course_aliases) — instead
of the old hand-maintained SUBJECT_MAP in core/router.py, which went
stale the moment a new semester's courses showed up (that's why "daa"
never resolved: BCSE204L/Design and Analysis of Algorithms was never
added there by hand).
"""

from rapidfuzz import fuzz

MATCH_THRESHOLD = 75
TOKEN_OVERLAP_BOOST = 20


def _tokens(s: str) -> set:
    return set((s or "").lower().replace("-", " ").split())


def resolve_course(query: str) -> list:
    """
    Returns [{course_code, course_name, short_name, score}, ...] for every
    known course scoring >= MATCH_THRESHOLD against the query, best match
    first. Checks course_name, short_name, course_code, and every
    user-taught alias for each course; an exact (case-insensitive) hit on
    any of those is scored 100, otherwise the best fuzzy ratio across all
    of them, boosted when every query token appears in the candidate
    (so "daa" against "Design and Analysis of Algorithms" — which
    wouldn't otherwise score highly on ratio alone — still wins once its
    token is checked against the short_name "DAA").
    """
    if not query or not query.strip():
        return []

    from core.memory import get_all_course_aliases

    q = query.strip().lower()
    q_tokens = _tokens(q)

    results = []
    for row in get_all_course_aliases():
        candidates = [row.get("course_name"), row.get("short_name"), row.get("course_code")]
        candidates += row.get("aliases") or []

        best_score = 0
        for cand in candidates:
            if not cand:
                continue
            cand_l = cand.lower()
            if cand_l == q:
                best_score = 100
                break
            score = fuzz.WRatio(q, cand_l)
            if q_tokens and q_tokens.issubset(_tokens(cand_l)):
                score = min(100, score + TOKEN_OVERLAP_BOOST)
            best_score = max(best_score, score)

        if best_score >= MATCH_THRESHOLD:
            results.append({
                "course_code": row.get("course_code"),
                "course_name": row.get("course_name"),
                "short_name": row.get("short_name"),
                "score": best_score,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def resolve_course_best(query: str, margin: int = 10):
    """
    Convenience wrapper for callers that just want one confident answer.
    Returns the top match dict if there's a clear winner (only one match
    at/above threshold, or the top score beats the runner-up by `margin`
    or more) — otherwise None, so an ambiguous or no-match query falls
    through to the caller's existing behavior rather than guessing wrong.
    """
    matches = resolve_course(query)
    if not matches:
        return None
    if len(matches) == 1 or matches[0]["score"] - matches[1]["score"] >= margin:
        return matches[0]
    return None
