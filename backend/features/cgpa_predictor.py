"""
features/cgpa_predictor.py — CGPA prediction and grade-target reverse-solving.

ASSUMPTIONS — confirmed with the author, flagged here for visibility:
  - Grade-point scale: S=10, A=9, B=8, C=7, D=6, E=5, F=0 (VIT's standard
    10-point scale).
  - Absolute grading cutoffs (used only for LAB courses — see below):
        S: 90-100, A: 80-89, B: 70-79, C: 60-69, D: 50-59, E: 45-49, F: <45
  - Theory courses at VIT are relatively graded (curve-based) per the author —
    required_mark() can't reverse-solve an exact score for these
    without the whole class's distribution, which no VTOP endpoint
    exposes. It returns error="relative_grading" instead of a
    fabricated number. Lab courses use the absolute scale above.
  - A course is treated as Lab if its code ends with 'P', Theory
    otherwise — matching core/router.py's existing (working) marks
    filter convention. Note: core/memory.py's course_type_label() says
    the opposite (L=Lab) — that looks like a pre-existing bug/
    inconsistency between the two files; this module deliberately
    follows the router's convention since that backs the
    already-working marks feature.
  - predict_cgpa()/what_if_all_S() need this semester's course
    credits. VTOP's academic-history view doesn't expose per-course
    credits at all (only a cumulative CGPA + total credits), so these
    are sourced from the timetable instead — see
    get_current_sem_course_credits() in core/memory.py. The timetable
    parser was extended (with the author's sign-off) to also try to
    extract a credits column; if VTOP's real column name doesn't match
    what it tries, credits come back empty and these functions return
    a clear error rather than silently guessing.
"""

import re

from features.base import FeatureResult

GRADE_POINTS = {"S": 10, "A": 9, "B": 8, "C": 7, "D": 6, "E": 5, "F": 0}

ABSOLUTE_CUTOFFS = [
    ("S", 90), ("A", 80), ("B", 70), ("C", 60), ("D", 50), ("E", 45), ("F", 0),
]


def _is_lab_course(course_code: str) -> bool:
    return (course_code or "").strip().upper().endswith("P")


def _normalize_grade_input(raw) -> str | None:
    """
    Accepts a letter grade (already valid) or a numeric grade-point
    (e.g. "score 9" -> 9) and returns the matching letter on this
    10-point scale (S=10..F=0) — the router prompt is told to do this
    conversion itself, but the LLM won't always comply, so this is a
    defensive fallback. Returns None if it can't be resolved at all.
    """
    if raw is None:
        return None
    raw_str = str(raw).strip().upper()
    if raw_str in GRADE_POINTS:
        return raw_str
    try:
        points = float(raw_str)
    except (ValueError, TypeError):
        return None
    return min(GRADE_POINTS, key=lambda g: abs(GRADE_POINTS[g] - points))


def _grade_from_text(user_input: str) -> str | None:
    """The grade named in the sentence ("if I get S in DAA", "an A in OS",
    "if I score 9 in everything"), for when no classifier extracted it —
    the keyword fallback that runs while the models are rate limited."""
    from core.router import detect_grade_filter
    letters = detect_grade_filter(user_input or "")[0] - {"N", "P"}
    if len(letters) == 1:
        return next(iter(letters))
    m = re.search(r"\b(?:get|got|score|scoring)\s+(?:an?\s+)?(10|[5-9])\b", user_input or "", re.I)
    return _normalize_grade_input(m.group(1)) if m else None


def _resolve_course_code(course_query: str) -> str | None:
    """
    Resolves a spoken course name/abbreviation to a course_code via
    vtop_marks. Normalizes through the same SUBJECT_MAP the marks
    feature uses first (so "TOC"/"DBMS"-style abbreviations match),
    since a raw substring match won't catch those.
    """
    from core.memory import get_db
    from core.router import normalize_course_query

    search_term = normalize_course_query(course_query)
    conn = get_db()
    row = conn.execute("""
        SELECT course_code FROM vtop_marks
        WHERE LOWER(course_title) LIKE LOWER(?) OR LOWER(course_code) LIKE LOWER(?)
        ORDER BY semester_id DESC LIMIT 1
    """, (f"%{search_term}%", f"%{search_term}%")).fetchone()
    conn.close()
    return row["course_code"] if row else None


# ══════════════════════════════════════════
#   CORE LOGIC
# ══════════════════════════════════════════

def predict_cgpa(hypothetical_grades: dict) -> dict:
    """
    hypothetical_grades: {course_code: grade_letter} for CURRENT
    semester courses (not yet graded/locked into VTOP's CGPA).

    Formula (sidesteps needing historical per-course credits, which
    VTOP's academic-history view doesn't expose — current CGPA +
    credits_registered already aggregate all of that):
        new_points  = current_CGPA * credits_registered_so_far
                      + sum(this_sem_credits_i * grade_points_i)
        new_credits = credits_registered_so_far + sum(this_sem_credits_i)
        new_CGPA    = new_points / new_credits
    """
    from core.memory import get_latest_cgpa_summary, get_current_sem_course_credits

    summary = get_latest_cgpa_summary()
    if not summary or summary.get("cgpa") is None or summary.get("credits_registered") is None:
        return {"ok": False, "error": "no_cgpa_baseline"}

    credits_map = get_current_sem_course_credits()
    if not credits_map:
        return {"ok": False, "error": "no_credits_data"}

    current_cgpa    = summary["cgpa"]
    current_credits = summary["credits_registered"]

    added_points    = 0.0
    added_credits   = 0.0
    missing_courses = []
    per_course      = []

    for course_code, grade in hypothetical_grades.items():
        credits = credits_map.get(course_code)
        if credits is None:
            missing_courses.append(course_code)
            continue
        points = GRADE_POINTS.get((grade or "").upper())
        if points is None:
            continue
        added_points  += credits * points
        added_credits += credits
        per_course.append({"course_code": course_code, "credits": credits, "grade": grade.upper()})

    if added_credits == 0:
        return {"ok": False, "error": "no_matching_courses", "missing_courses": missing_courses}

    new_credits = current_credits + added_credits
    new_points  = current_cgpa * current_credits + added_points
    new_cgpa    = round(new_points / new_credits, 3)

    return {
        "ok": True,
        "current_cgpa": current_cgpa,
        "new_cgpa": new_cgpa,
        "per_course": per_course,
        "missing_courses": missing_courses,
    }


def what_if_all_S() -> dict:
    """Best-case CGPA if every current-sem course (with known credits) gets an S."""
    from core.memory import get_current_sem_course_credits
    credits_map = get_current_sem_course_credits()
    if not credits_map:
        return {"ok": False, "error": "no_credits_data"}
    return predict_cgpa({code: "S" for code in credits_map})


def required_overall_gpa(target_cgpa: float) -> dict:
    """
    "how much should I score to get a 9 CGPA overall" — the inverse of
    predict_cgpa(): given a target OVERALL CGPA, solves for the
    credit-weighted average grade-point this semester needs as a whole
    to reach it, using the same current-CGPA + credits_registered
    baseline and this-semester credits as predict_cgpa().

        target_cgpa * (current_credits + this_sem_credits)
            = current_cgpa * current_credits + this_sem_credits * required_avg_gpa
        required_avg_gpa = (target_cgpa * (current_credits + this_sem_credits)
                             - current_cgpa * current_credits) / this_sem_credits
    """
    from core.memory import get_latest_cgpa_summary, get_current_sem_course_credits

    summary = get_latest_cgpa_summary()
    if not summary or summary.get("cgpa") is None or summary.get("credits_registered") is None:
        return {"ok": False, "error": "no_cgpa_baseline"}

    credits_map = get_current_sem_course_credits()
    if not credits_map:
        return {"ok": False, "error": "no_credits_data"}

    current_cgpa      = summary["cgpa"]
    current_credits   = summary["credits_registered"]
    this_sem_credits  = sum(credits_map.values())

    if this_sem_credits == 0:
        return {"ok": False, "error": "no_credits_data"}

    required_points_sum = (target_cgpa * (current_credits + this_sem_credits)
                            - current_cgpa * current_credits)
    required_avg_gpa = required_points_sum / this_sem_credits

    already_achieved = required_avg_gpa <= 0
    impossible       = required_avg_gpa > max(GRADE_POINTS.values())

    # Smallest grade whose point value still meets/exceeds the
    # required average — a safe "average at least this" recommendation,
    # not a literal per-course grade (GPA is a weighted average across
    # different courses/credits, not one grade repeated everywhere).
    nearest_grade = None
    if not impossible and not already_achieved:
        for grade, points in sorted(GRADE_POINTS.items(), key=lambda kv: kv[1]):
            if points >= required_avg_gpa:
                nearest_grade = grade
                break

    return {
        "ok": True,
        "current_cgpa": current_cgpa,
        "target_cgpa": target_cgpa,
        "this_sem_credits": this_sem_credits,
        "required_avg_gpa": round(required_avg_gpa, 3),
        "nearest_grade": nearest_grade,
        "already_achieved": already_achieved,
        "impossible": impossible,
    }


def required_mark(course_code: str, target_grade: str) -> dict:
    """
    Reverse-solves the raw mark needed in the largest still-unscored
    assessment (typically FAT) to reach target_grade, using each
    assessment's weightage_percent/max_mark from vtop_marks.

    Only meaningful for LAB courses (absolute grading). For theory
    courses (relative grading), returns ok=False, error="relative_grading".
    """
    from core.memory import get_db

    target_grade  = (target_grade or "").upper()
    target_points = GRADE_POINTS.get(target_grade)
    if target_points is None:
        return {"ok": False, "error": "unknown_grade"}

    if not _is_lab_course(course_code):
        return {
            "ok": False, "error": "relative_grading",
            "message": ("This looks like a theory course, which VIT grades on a curve — "
                        "I can't reverse-solve an exact required mark without the whole "
                        "class's score distribution, which VTOP doesn't expose."),
        }

    target_cutoff = next((cutoff for grade, cutoff in ABSOLUTE_CUTOFFS if grade == target_grade), None)
    if target_cutoff is None:
        return {"ok": False, "error": "unknown_grade"}

    conn = get_db()
    rows = conn.execute("""
        SELECT mark_title, max_mark, scored_mark, weightage_percent, weightage_mark
        FROM vtop_marks
        WHERE course_code = ? AND mark_title != 'NO_MARKS_YET'
    """, (course_code,)).fetchall()
    conn.close()

    if not rows:
        return {"ok": False, "error": "no_marks_data"}

    scored_weighted = 0.0
    unscored = []
    for r in rows:
        if r["scored_mark"] is not None and r["weightage_mark"] is not None:
            scored_weighted += r["weightage_mark"]
        elif r["scored_mark"] is None and r["weightage_percent"] is not None and r["max_mark"]:
            unscored.append(dict(r))

    if not unscored:
        return {"ok": False, "error": "already_fully_scored", "scored_weighted": round(scored_weighted, 2)}

    # If multiple assessments are still unscored, solve for the
    # largest-weightage one (typically FAT), assuming the others stay at 0.
    target_assessment = max(unscored, key=lambda r: r["weightage_percent"])

    needed_weighted = target_cutoff - scored_weighted
    if needed_weighted <= 0:
        return {"ok": True, "already_safe": True, "target_grade": target_grade,
                "assessment": target_assessment["mark_title"], "required_raw_mark": 0}

    max_mark          = target_assessment["max_mark"]
    weightage_percent = target_assessment["weightage_percent"]
    required_raw      = (needed_weighted / weightage_percent) * max_mark

    return {
        "ok": True,
        "target_grade": target_grade,
        "assessment": target_assessment["mark_title"],
        "max_mark": max_mark,
        "required_raw_mark": round(required_raw, 1),
        "impossible": required_raw > max_mark,
        "other_unscored": [r["mark_title"] for r in unscored if r is not target_assessment],
    }


# ══════════════════════════════════════════
#   PUBLIC FEATURE-RESULT API (used by jarvis.py)
# ══════════════════════════════════════════

def get_grade_target_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """"what CAT-2 do I need in TOC for an S" — entities: {course, target_grade}."""
    entities     = entities or {}
    course_query = entities.get("course")
    target_grade = entities.get("target_grade") or _grade_from_text(user_input)

    if not course_query or not target_grade:
        msg = "Tell me the course and target grade — e.g. 'what CAT2 do I need in TOC for an S'."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="missing_entity")

    course_code = _resolve_course_code(course_query)
    if not course_code:
        msg = f"I couldn't find a course matching '{course_query}'."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="course_not_found")

    result = required_mark(course_code, target_grade)

    if not result["ok"]:
        err = result["error"]
        if err == "relative_grading":
            msg = result["message"]
        elif err == "no_marks_data":
            msg = f"I don't have marks data for {course_query} yet."
        elif err == "already_fully_scored":
            msg = f"All assessments in {course_query} are already scored — nothing left to reverse-solve."
        else:
            msg = "Couldn't work that out."
        return FeatureResult(ok=False, data=result, display=msg, spoken=msg, error=err)

    if result.get("already_safe"):
        msg = f"You've already secured at least a {result['target_grade']} in {course_query}."
        return FeatureResult(ok=True, data=result, display=msg, spoken=msg)

    req, assess, maxm = result["required_raw_mark"], result["assessment"], result["max_mark"]
    if result["impossible"]:
        msg    = f"Not mathematically possible — you'd need {req}/{maxm} in {assess}, above the max."
        spoken = f"Sorry boss, an {result['target_grade']} isn't possible anymore in {course_query}."
    else:
        msg    = f"You need {req}/{maxm} in {assess} to get an {result['target_grade']} in {course_query}."
        spoken = f"You need {req} out of {maxm} in {assess} for an {result['target_grade']} in {course_query}."
    return FeatureResult(ok=True, data=result, display=msg, spoken=spoken)


def get_best_case_cgpa_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """"if I get A in everything" / "if I score 9 this sem" — entities: {grade} (default S)."""
    entities = entities or {}
    grade    = _normalize_grade_input(entities.get("grade")) or _grade_from_text(user_input) or "S"

    from core.memory import get_current_sem_course_credits
    credits_map = get_current_sem_course_credits()
    if not credits_map:
        msg = ("I don't have this semester's course credits yet — make sure your timetable's "
               "synced (say 'schedule today') and try again.")
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_credits_data")

    result = predict_cgpa({code: grade for code in credits_map})
    if not result["ok"]:
        msg = "Couldn't compute that — I'm missing your current CGPA baseline or course credits."
        return FeatureResult(ok=False, data=result, display=msg, spoken=msg, error=result.get("error"))

    display = (f"If you get {grade} in every course this semester:\n"
               f"  Current CGPA: {result['current_cgpa']}\n"
               f"  Projected CGPA: {result['new_cgpa']}")
    spoken = f"If you get {grade} in everything this sem, your CGPA moves to {result['new_cgpa']}."
    return FeatureResult(ok=True, data=result, display=display, spoken=spoken)


def get_cgpa_predict_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """
    "what's my CGPA if I get A in DBMS" — entities: {course, grade}.
    For a uniform "everything" hypothetical, best_case_cgpa is used
    instead (see handle_best_case_cgpa in jarvis.py).
    """
    entities     = entities or {}
    course_query = entities.get("course")
    grade        = _normalize_grade_input(entities.get("grade")) or _grade_from_text(user_input)

    if not course_query or not grade:
        msg = "Tell me a course and a grade, or ask 'what's my CGPA if I get S in everything'."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="missing_entity")

    course_code = _resolve_course_code(course_query)
    if not course_code:
        msg = f"I couldn't find a course matching '{course_query}'."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="course_not_found")

    result = predict_cgpa({course_code: grade})
    if not result["ok"]:
        err = result["error"]
        if err == "no_credits_data":
            msg = "I don't have this semester's course credits yet."
        elif err == "no_matching_courses":
            msg = f"I don't have credits for {course_query} this semester — can't compute that."
        else:
            msg = "Couldn't compute that — missing your CGPA baseline."
        return FeatureResult(ok=False, data=result, display=msg, spoken=msg, error=err)

    display = (f"If you get {grade.upper()} in {course_query}: "
               f"CGPA moves from {result['current_cgpa']} to {result['new_cgpa']}.")
    spoken = f"That would move your CGPA to {result['new_cgpa']}."
    return FeatureResult(ok=True, data=result, display=display, spoken=spoken)


def get_required_overall_gpa_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    """"how much should I score to get a 9 CGPA overall" — entities: {target_cgpa}."""
    entities = entities or {}
    raw      = entities.get("target_cgpa")
    if raw is None:
        m = re.search(r"\b(\d{1,2}(?:\.\d+)?)\s*(?:\+\s*)?(?:cgpa|pointer)\b|\bcgpa\s+(?:of\s+)?(\d{1,2}(?:\.\d+)?)\b",
                      user_input or "", re.I)
        raw = (m.group(1) or m.group(2)) if m else None
    try:
        target_cgpa = float(raw)
    except (TypeError, ValueError):
        msg = "Tell me a target CGPA — e.g. 'how much do I need to score to get a 9 CGPA'."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="missing_entity")

    result = required_overall_gpa(target_cgpa)
    if not result["ok"]:
        err = result["error"]
        if err == "no_credits_data":
            msg = ("I don't have this semester's course credits yet — make sure your timetable's "
                   "synced (say 'schedule today') and try again.")
        else:
            msg = "Couldn't compute that — missing your current CGPA baseline."
        return FeatureResult(ok=False, data=result, display=msg, spoken=msg, error=err)

    if result["already_achieved"]:
        msg = f"You're already there — your current CGPA of {result['current_cgpa']} already meets {target_cgpa}."
        return FeatureResult(ok=True, data=result, display=msg, spoken=msg)

    if result["impossible"]:
        msg = (f"Not possible this semester alone — even acing everything (all S) "
               f"wouldn't reach {target_cgpa} from {result['current_cgpa']}. You'd need it over multiple semesters.")
        return FeatureResult(ok=True, data=result, display=msg, spoken=msg)

    display = (f"To reach an overall CGPA of {target_cgpa} (currently {result['current_cgpa']}), "
               f"you need to average at least {result['required_avg_gpa']} grade points across "
               f"this semester's {result['this_sem_credits']} credits — roughly an "
               f"{result['nearest_grade']} average or better.")
    spoken = (f"You need roughly an {result['nearest_grade']} average this semester "
              f"to hit a {target_cgpa} CGPA overall.")
    return FeatureResult(ok=True, data=result, display=display, spoken=spoken)
