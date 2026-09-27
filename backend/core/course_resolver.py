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

import re

from rapidfuzz import fuzz

MATCH_THRESHOLD = 75
TOKEN_OVERLAP_BOOST = 20
# A whole sentence ("what did i get in dbms cat1") is scored differently
# from a bare course reference ("dbms"): fuzzy-matching the sentence as a
# whole let WRatio's partial matching find short names inside unrelated
# words — that sentence resolved to "Spanish I" (short name SI), and
# "show my marks in detail" to Differential Equations (DET ⊂ "detail").
SENTENCE_MODE_MIN_WORDS = 4
SENTENCE_MATCH_THRESHOLD = 85
SHORT_CANDIDATE_MAX_LEN = 4
_GENERIC_LEAD_WORDS = {"advanced", "introduction", "computer", "engineering", "technical", "principles",
                       "foundations", "fundamentals", "applied", "software", "operating", "database",
                       "digital", "structured", "quantitative", "qualitative", "environmental"}


def _tokens(s: str) -> set:
    return set((s or "").lower().replace("-", " ").split())


def _sentence_score(q: str, words: list, cand_l: str) -> int:
    """Score a candidate against a full sentence: short names/codes must
    appear as a whole word; longer names are compared against same-sized
    word windows of the sentence, never the whole thing."""
    if re.search(rf"(?<!\w){re.escape(cand_l)}(?!\w)", q):
        return 100
    if len(cand_l) <= SHORT_CANDIDATE_MAX_LEN:
        return 0
    # "...miss in probability" -> Probability and Statistics: the course
    # name's leading distinctive word said on its own.
    lead = cand_l.split()[0]
    if len(lead) >= 7 and lead not in _GENERIC_LEAD_WORDS and re.search(rf"(?<!\w){re.escape(lead)}(?!\w)", q):
        return 90
    n = len(cand_l.split())
    best = 0
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(len(words) - size + 1):
            best = max(best, fuzz.ratio(" ".join(words[i:i + size]), cand_l))
    return best


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

    q = " ".join(re.findall(r"[a-z0-9&+#]+", query.lower()))
    q_tokens = _tokens(q)
    words = q.split()
    sentence_mode = len(words) >= SENTENCE_MODE_MIN_WORDS
    threshold = SENTENCE_MATCH_THRESHOLD if sentence_mode else MATCH_THRESHOLD

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
            if sentence_mode:
                score = _sentence_score(q, words, cand_l)
            elif len(cand_l) <= SHORT_CANDIDATE_MAX_LEN or len(q) <= SHORT_CANDIDATE_MAX_LEN:
                # WRatio's partial matching on 2-4 letter strings is noise
                # ("toc" scored as Calculus); plain ratio only.
                score = fuzz.ratio(q, cand_l)
            else:
                score = fuzz.WRatio(q, cand_l)
                if q_tokens and q_tokens.issubset(_tokens(cand_l)):
                    score = min(100, score + TOKEN_OVERLAP_BOOST)
            best_score = max(best_score, score)

        if best_score >= threshold:
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
    lab_re = re.compile(r"\b(?:lab|labs|practical|laboratory)\b", re.IGNORECASE)
    if lab_re.search(query):
        # "daa lab" / "cd lab": resolve the course itself, then hop to its
        # lab sibling ("<same name> Lab") — the lab's own short name
        # (DAAL, CDL) is rarely what gets said.
        base = resolve_course_best(" ".join(lab_re.sub(" ", query).split()), margin)
        if base:
            if _is_lab(base["course_name"]):
                return base
            sibling = _lab_sibling(base["course_name"])
            if sibling:
                return {**sibling, "score": base["score"]}

    matches = resolve_course(query)
    if not matches:
        return None
    # "compiler design" matches both the theory course and its lab at 100;
    # theory is the default unless the lab was asked for (handled above).
    theory = [m for m in matches if not _is_lab(m["course_name"])]
    if theory and len(theory) < len(matches) and not lab_re.search(query):
        matches = theory
    if len(matches) == 1 or matches[0]["score"] - matches[1]["score"] >= margin:
        best = matches[0]
        if lab_re.search(query) and not _is_lab(best["course_name"]):
            sibling = _lab_sibling(best["course_name"])
            if sibling:
                return {**sibling, "score": best["score"]}
        return best
    return None


def _is_lab(course_name) -> bool:
    return bool(re.search(r"\blab\b", (course_name or "").lower()))


def _lab_sibling(theory_name: str):
    from core.memory import get_all_course_aliases
    theory_name = (theory_name or "").lower()
    for row in get_all_course_aliases():
        name = (row.get("course_name") or "").lower()
        if name.startswith(theory_name) and _is_lab(name):
            return {"course_code": row.get("course_code"), "course_name": row.get("course_name"),
                    "short_name": row.get("short_name")}
    return None
