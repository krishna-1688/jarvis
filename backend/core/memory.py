"""
memory.py — Complete memory system for Jarvis.

vtop_marks table: ONE ROW per (semester_id, course_code, mark_title)
Search: fuzzy LIKE on course_code + course_title across all stored data
NO clearing on re-sync — pure upsert only. Completed sems never touched again.
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta

# ── Paths ──────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# JARVIS_DB lets tests run against a copy instead of your real data.
DB_PATH     = os.environ.get("JARVIS_DB") or os.path.join(BASE_DIR, "data", "database", "jarvis.db")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


# ── Semester ID → Label ────────────────────────────────
# Per student, from admission year + today's date — see core/semesters.py.
from core.semesters import SEM_LABELS, CURRENT_SEM  # noqa: E402

def sem_label(sem_id: str) -> str:
    return SEM_LABELS.get(sem_id, sem_id)

def course_type_label(course_code: str) -> str:
    """L suffix = Lab, P suffix = Practical/Project, else Theory."""
    if not course_code:
        return ""
    code = course_code.strip().upper()
    if code.endswith("L"):
        return "Lab"
    elif code.endswith("P"):
        return "Practical"
    else:
        return "Theory"

# ── SQLite ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_msg   TEXT NOT NULL,
            jarvis_msg TEXT NOT NULL,
            topic      TEXT DEFAULT 'general',
            timestamp  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS personal_facts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            category   TEXT DEFAULT 'notes',
            key_name   TEXT NOT NULL,
            value      TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(category, key_name)
        );

        CREATE TABLE IF NOT EXISTS vtop_marks (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_id       TEXT NOT NULL,
            course_code       TEXT NOT NULL,
            course_title      TEXT NOT NULL,
            course_type       TEXT,
            faculty           TEXT,
            slot              TEXT,
            mark_title        TEXT NOT NULL,
            max_mark          REAL,
            scored_mark       REAL,
            weightage_percent REAL,
            weightage_mark    REAL,
            status            TEXT,
            synced_at         TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(semester_id, course_code, mark_title)
        );

        CREATE TABLE IF NOT EXISTS vtop_sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            data_type   TEXT NOT NULL,
            semester_id TEXT NOT NULL,
            synced_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(data_type, semester_id)
        );

        CREATE TABLE IF NOT EXISTS system_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS course_aliases (
            course_code TEXT PRIMARY KEY,
            course_name TEXT NOT NULL,
            short_name  TEXT,
            aliases     TEXT DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS vtop_timetable (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            day          TEXT NOT NULL,
            start_time   TEXT,
            end_time     TEXT,
            course_code  TEXT,
            course_name  TEXT,
            room         TEXT,
            slot         TEXT,
            faculty      TEXT,
            credits      REAL,
            synced_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS vtop_grades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            semester_id  TEXT,
            course_code  TEXT,
            course_name  TEXT NOT NULL,
            credits      REAL,
            grade        TEXT,
            grade_points REAL,
            synced_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(course_name)
        );

        CREATE TABLE IF NOT EXISTS vtop_cgpa_summary (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            cgpa               REAL,
            credits_registered REAL,
            credits_earned     REAL,
            extra_json         TEXT,
            synced_at          TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS vtop_exams (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_type    TEXT,
            course_code  TEXT,
            course_name  TEXT,
            exam_date    TEXT,
            session      TEXT,
            venue        TEXT,
            seat_number  TEXT,
            synced_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS vtop_attendance (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            course_code      TEXT NOT NULL,
            course_name      TEXT NOT NULL,
            slot             TEXT,
            total_classes    INTEGER,
            attended_classes INTEGER,
            percentage       REAL,
            last_synced      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(course_code)
        );

        CREATE TABLE IF NOT EXISTS timetable (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            day     TEXT NOT NULL,
            subject TEXT NOT NULL,
            time    TEXT NOT NULL,
            room    TEXT,
            faculty TEXT,
            slot    TEXT
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            course_code TEXT,
            subject     TEXT NOT NULL UNIQUE,
            attended    INTEGER DEFAULT 0,
            total       INTEGER DEFAULT 0,
            percentage  REAL DEFAULT 0,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS exam_schedule (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            course_code  TEXT,
            course_title TEXT,
            exam_date    TEXT,
            session      TEXT,
            venue        TEXT,
            updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            notes        TEXT,
            due_at       TEXT,
            priority     TEXT DEFAULT 'normal',
            status       TEXT DEFAULT 'open',
            tag          TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            trigger_time TEXT,
            recurring    INTEGER DEFAULT 0,
            done         INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            subject    TEXT NOT NULL,
            title      TEXT NOT NULL,
            deadline   TEXT,
            source     TEXT DEFAULT 'moodle',
            done       INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            amount      REAL NOT NULL,
            currency    TEXT DEFAULT 'INR',
            category    TEXT,
            merchant    TEXT,
            note        TEXT,
            source      TEXT DEFAULT 'voice',
            raw_text    TEXT,
            spent_at    TEXT,
            logged_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS merchant_aliases (
            vpa           TEXT PRIMARY KEY,
            friendly_name TEXT NOT NULL,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bills (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL,
            amount    REAL,
            due_date  TEXT,
            recurring INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS splits (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            person      TEXT NOT NULL,
            amount      REAL NOT NULL,
            description TEXT,
            they_owe    INTEGER DEFAULT 1,
            date        TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS health_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            type      TEXT NOT NULL,
            details   TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS preferences (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
           CREATE TABLE IF NOT EXISTS lms_assignments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            course_name     TEXT NOT NULL,
            course_url      TEXT,
            due_date        TEXT NOT NULL,
            due_date_str    TEXT,
            assign_url      TEXT,
            description     TEXT,
            status          TEXT DEFAULT 'not_submitted',
            reminded_3day   INTEGER DEFAULT 0,
            reminded_1day   INTEGER DEFAULT 0,
            synced_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(title, course_name)
        );
 
        CREATE TABLE IF NOT EXISTS lms_sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS focus_sessions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            linked_block_id     INTEGER,
            subject             TEXT,
            started_at          TEXT,
            ended_at            TEXT,
            planned_minutes     INTEGER,
            actual_minutes      INTEGER,
            pomodoros_completed INTEGER DEFAULT 0,
            status              TEXT DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS schedule_blocks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            title          TEXT NOT NULL,
            start_at       TEXT NOT NULL,
            end_at         TEXT NOT NULL,
            block_type     TEXT DEFAULT 'custom',
            linked_course  TEXT,
            linked_task_id INTEGER,
            notes          TEXT,
            status         TEXT DEFAULT 'planned',
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # One-time migration: an earlier, never-wired `tasks` stub (title/priority/
    # deadline/done) predates the real Phase 4 schema. It was never used by any
    # feature (confirmed empty), so replace it rather than carry two schemas.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if cols and "due_at" not in cols:
        conn.execute("DROP TABLE tasks")
        conn.execute("""
            CREATE TABLE tasks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                notes        TEXT,
                due_at       TEXT,
                priority     TEXT DEFAULT 'normal',
                status       TEXT DEFAULT 'open',
                tag          TEXT,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            )
        """)

    # Same story for `expenses` (Phase 5): an earlier, never-wired stub
    # (amount/category/description/date) predates the real schema.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)").fetchall()}
    if cols and "spent_at" not in cols:
        conn.execute("DROP TABLE expenses")
        conn.execute("""
            CREATE TABLE expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                amount      REAL NOT NULL,
                currency    TEXT DEFAULT 'INR',
                category    TEXT,
                merchant    TEXT,
                note        TEXT,
                source      TEXT DEFAULT 'voice',
                raw_text    TEXT,
                spent_at    TEXT,
                logged_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

    # H.4: track which semester's snapshot the timetable/exam/attendance
    # tables hold, so a semester rollover (CURRENT_SEM bumped in
    # constants.py) can be detected instead of silently mixing data from
    # two semesters. These tables shipped before this column existed, so
    # existing DBs need it bolted on.
    for _table in ("vtop_timetable", "vtop_exams", "vtop_attendance"):
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({_table})").fetchall()}
        if "semester_id" not in cols:
            conn.execute(f"ALTER TABLE {_table} ADD COLUMN semester_id TEXT")

    # vtop_attendance now stores one permanent row per (course_code,
    # semester_id) instead of one row per course_code overall — a
    # completed semester's attendance never changes once fetched, so
    # it's worth keeping forever rather than re-fetching, mirroring
    # vtop_marks' existing per-semester design (see H.4's semester_id
    # column above and features/vtop.py's fetch_attendance_all_sems).
    # The old UNIQUE(course_code) constraint would let a later
    # semester's sync silently overwrite an earlier semester's row for
    # the same course code, so the table needs recreating with the
    # wider constraint — SQLite can't ALTER a UNIQUE constraint in place.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='vtop_attendance'"
    ).fetchone()
    if row and "UNIQUE(course_code, semester_id)" not in (row["sql"] or ""):
        conn.executescript("""
            ALTER TABLE vtop_attendance RENAME TO vtop_attendance_pre_h7;
            CREATE TABLE vtop_attendance (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                course_code      TEXT NOT NULL,
                course_name      TEXT NOT NULL,
                slot             TEXT,
                total_classes    INTEGER,
                attended_classes INTEGER,
                percentage       REAL,
                last_synced      TEXT DEFAULT CURRENT_TIMESTAMP,
                semester_id      TEXT,
                UNIQUE(course_code, semester_id)
            );
            INSERT INTO vtop_attendance
                (id, course_code, course_name, slot, total_classes, attended_classes,
                 percentage, last_synced, semester_id)
            SELECT id, course_code, course_name, slot, total_classes, attended_classes,
                   percentage, last_synced, semester_id
            FROM vtop_attendance_pre_h7;
            DROP TABLE vtop_attendance_pre_h7;
        """)

    # Same story for vtop_exams — completed-semester exam schedules never
    # change either, so they're worth keeping forever (get_all_exams now
    # takes a semester_id) instead of the old wipe-on-every-sync design,
    # which meant a past semester's exam dates were simply gone the
    # moment the current semester's schedule got synced. A course can
    # have several exam TYPES (CAT1/CAT2/FAT) in one semester, so the
    # uniqueness key is (course_code, exam_type, semester_id), not just
    # (course_code, semester_id) like attendance.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='vtop_exams'"
    ).fetchone()
    if row and "UNIQUE(course_code, exam_type, semester_id)" not in (row["sql"] or ""):
        conn.executescript("""
            ALTER TABLE vtop_exams RENAME TO vtop_exams_pre_h8;
            CREATE TABLE vtop_exams (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_type    TEXT,
                course_code  TEXT,
                course_name  TEXT,
                exam_date    TEXT,
                session      TEXT,
                venue        TEXT,
                seat_number  TEXT,
                synced_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                semester_id  TEXT,
                UNIQUE(course_code, exam_type, semester_id)
            );
            INSERT OR IGNORE INTO vtop_exams
                (id, exam_type, course_code, course_name, exam_date,
                 session, venue, seat_number, synced_at, semester_id)
            SELECT id, exam_type, course_code, course_name, exam_date,
                   session, venue, seat_number, synced_at, semester_id
            FROM vtop_exams_pre_h8;
            DROP TABLE vtop_exams_pre_h8;
        """)

    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════
#   SEMESTER ROLLOVER — archive + reset when CURRENT_SEM changes
# ══════════════════════════════════════════════════════════

def reset_semester_data(new_sem_id: str):
    """
    Archives the current timetable/exam/attendance snapshot into `_old`
    tables (each row keeps whatever semester_id it was synced under)
    instead of deleting it outright, then clears the live tables and their
    sync_log entries so the next fetch is treated as a cold start rather
    than "already fresh." Marks are untouched — they're keyed by
    (semester_id, course_code, mark_title) and never wiped by design.

    Also drops every auto-materialized 'class' schedule_block — those are
    pure derivatives of vtop_timetable (see materialize_classes_for_date)
    keyed by (course_code, start_at), so once the course codes change
    under a semester rollover they'd never get deduped against the old
    ones and would just pile up as duplicate/stale entries forever.
    User-created blocks (custom/task/focus) are untouched.
    """
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vtop_timetable_old  AS SELECT * FROM vtop_timetable  WHERE 0;
        CREATE TABLE IF NOT EXISTS vtop_exams_old      AS SELECT * FROM vtop_exams      WHERE 0;
        CREATE TABLE IF NOT EXISTS vtop_attendance_old AS SELECT * FROM vtop_attendance WHERE 0;

        INSERT INTO vtop_timetable_old  SELECT * FROM vtop_timetable;
        INSERT INTO vtop_exams_old      SELECT * FROM vtop_exams;
        INSERT INTO vtop_attendance_old SELECT * FROM vtop_attendance;

        DELETE FROM vtop_timetable;
        DELETE FROM vtop_exams;
        DELETE FROM vtop_attendance;
        DELETE FROM vtop_sync_log WHERE data_type IN ('timetable', 'exams', 'attendance');
        DELETE FROM schedule_blocks WHERE block_type = 'class';
    """)
    conn.execute("""
        INSERT INTO system_state (key, value) VALUES ('active_semester_id', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (new_sem_id,))
    conn.commit()
    conn.close()
    print(f"🔄 Semester rollover: archived old timetable/exam/attendance snapshot, now tracking {new_sem_id}")


def check_semester_rollover(current_sem_id: str = None) -> bool:
    """
    Compares system_state's stored active_semester_id against the app's
    configured current semester. Returns True if a rollover just happened
    (callers should trigger a fresh timetable/attendance/exam fetch),
    False if nothing changed. Call once at startup.
    """
    current_sem_id = current_sem_id or CURRENT_SEM
    conn = get_db()
    row = conn.execute("SELECT value FROM system_state WHERE key = 'active_semester_id'").fetchone()
    stored = row["value"] if row else None
    conn.close()

    if stored == current_sem_id:
        return False

    reset_semester_data(current_sem_id)
    return True


# ══════════════════════════════════════════════════════════
#   COURSE ALIASES — dynamic fuzzy-match source of truth (H.2)
#   Replaces the old hand-maintained SUBJECT_MAP in core/router.py,
#   which went stale the moment a new semester's courses showed up
#   (e.g. "daa" never resolving because DAA/BCSE204L was never added
#   there by hand). Auto-populated from whatever's actually in
#   vtop_timetable/vtop_attendance, so it can never lag behind reality.
# ══════════════════════════════════════════════════════════

_ALIAS_STOPWORDS = {"and", "of", "the", "in", "to", "for", "a", "an", "with", "on"}

def _generate_short_name(course_name: str) -> str:
    """Initials of the non-stopword words — 'Design and Analysis of
    Algorithms' -> 'DAA'. Purely a convenience label; the fuzzy resolver
    also matches on the full course_name, so this doesn't need to be
    perfect."""
    words = [w for w in re.split(r"[\s\-]+", course_name or "") if w]
    letters = [w[0].upper() for w in words if w.lower() not in _ALIAS_STOPWORDS and w[0].isalpha()]
    return "".join(letters)


def sync_course_aliases(courses: list):
    """
    courses: [(course_code, course_name), ...]. Upserts each into
    course_aliases — course_name is refreshed every time (VTOP's title
    formatting can shift slightly), but short_name/aliases are only set
    on first insert and never overwritten, so a custom alias the user
    taught (see add_course_alias) or a hand-edited short_name survives
    every future sync.
    """
    _invalidate_alias_cache()
    conn = get_db()
    for course_code, course_name in courses:
        course_code = (course_code or "").strip()
        course_name = (course_name or "").strip()
        if not course_code or not course_name:
            continue
        conn.execute("""
            INSERT INTO course_aliases (course_code, course_name, short_name, aliases)
            VALUES (?, ?, ?, '[]')
            ON CONFLICT(course_code) DO UPDATE SET course_name = excluded.course_name
        """, (course_code, course_name, _generate_short_name(course_name)))
    conn.commit()
    conn.close()


# Read on nearly every turn (router, course resolver, memory graph); the
# table only changes on a VTOP sync or a taught alias, both of which clear
# this cache.
_ALIAS_CACHE = {"at": 0.0, "rows": None}
_ALIAS_CACHE_TTL_S = 300


def _invalidate_alias_cache():
    _ALIAS_CACHE["rows"] = None


def get_all_course_aliases() -> list:
    import time as _time
    if _ALIAS_CACHE["rows"] is not None and _time.time() - _ALIAS_CACHE["at"] < _ALIAS_CACHE_TTL_S:
        return _ALIAS_CACHE["rows"]
    conn = get_db()
    rows = conn.execute("SELECT * FROM course_aliases").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["aliases"] = json.loads(d["aliases"]) if d["aliases"] else []
        except (json.JSONDecodeError, TypeError):
            d["aliases"] = []
        out.append(d)
    _ALIAS_CACHE.update(at=_time.time(), rows=out)
    return out


def add_course_alias(course_code: str, new_alias: str) -> bool:
    """Appends a user-taught alias ('remember DAA means Design and
    Analysis') to that course's aliases JSON array. Returns False if
    course_code isn't a known course."""
    _invalidate_alias_cache()
    new_alias = (new_alias or "").strip()
    if not new_alias:
        return False

    conn = get_db()
    row = conn.execute(
        "SELECT aliases FROM course_aliases WHERE course_code = ?", (course_code,)
    ).fetchone()
    if not row:
        conn.close()
        return False

    try:
        aliases = json.loads(row["aliases"]) if row["aliases"] else []
    except (json.JSONDecodeError, TypeError):
        aliases = []

    if new_alias.lower() not in (a.lower() for a in aliases):
        aliases.append(new_alias)
        conn.execute(
            "UPDATE course_aliases SET aliases = ? WHERE course_code = ?",
            (json.dumps(aliases), course_code)
        )
        conn.commit()

    conn.close()
    return True


# ══════════════════════════════════════════════════════════
#   VTOP MARKS — SAVE (pure upsert, never wipes)
# ══════════════════════════════════════════════════════════

def save_vtop_marks(parsed_marks: list, semester_id: str):
    """
    Save parsed marks from vtop_handler into SQLite.
    PURE UPSERT — never deletes existing rows.
    Completed sems are stored once and never touched again.
    Only current sem gets refreshed on daily sync.
    """
    conn  = get_db()
    now   = datetime.now().isoformat()
    count = 0

    def safe_float(v):
        try:
            return float(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    for subject in parsed_marks:
        course_code  = (subject.get("Course Code") or "").strip()
        course_title = (subject.get("Course Title") or "").strip()
        course_type  = subject.get("Course Type", "") or course_type_label(course_code)
        faculty      = subject.get("Faculty", "")
        slot         = subject.get("Slot", "")

        if not course_code:
            continue

        marks_list = subject.get("marks", [])

        if not marks_list:
            conn.execute("""
                INSERT INTO vtop_marks
                    (semester_id, course_code, course_title, course_type,
                     faculty, slot, mark_title, synced_at)
                VALUES (?,?,?,?,?,?,'NO_MARKS_YET',?)
                ON CONFLICT(semester_id, course_code, mark_title) DO UPDATE SET
                    synced_at = excluded.synced_at
            """, (semester_id, course_code, course_title, course_type,
                  faculty, slot, now))
            count += 1
            continue

        for mark in marks_list:
            mark_title = (mark.get("Mark Title") or "").strip()
            if not mark_title:
                continue
            conn.execute("""
                INSERT INTO vtop_marks
                    (semester_id, course_code, course_title, course_type,
                     faculty, slot, mark_title, max_mark, scored_mark,
                     weightage_percent, weightage_mark, status, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(semester_id, course_code, mark_title) DO UPDATE SET
                    course_title      = excluded.course_title,
                    course_type       = excluded.course_type,
                    faculty           = excluded.faculty,
                    slot              = excluded.slot,
                    max_mark          = excluded.max_mark,
                    scored_mark       = excluded.scored_mark,
                    weightage_percent = excluded.weightage_percent,
                    weightage_mark    = excluded.weightage_mark,
                    status            = excluded.status,
                    synced_at         = excluded.synced_at
            """, (
                semester_id, course_code, course_title, course_type,
                faculty, slot, mark_title,
                safe_float(mark.get("Max. Mark")),
                safe_float(mark.get("Scored Mark")),
                safe_float(mark.get("Weightage %")),
                safe_float(mark.get("Weightage Mark")),
                mark.get("Status", ""),
                now
            ))
            count += 1

    conn.execute("""
        INSERT INTO vtop_sync_log (data_type, semester_id, synced_at)
        VALUES ('marks', ?, ?)
        ON CONFLICT(data_type, semester_id) DO UPDATE SET synced_at = excluded.synced_at
    """, (semester_id, now))

    conn.commit()
    conn.close()
    print(f"✅ Saved/updated {count} mark rows for {sem_label(semester_id)}")


# ══════════════════════════════════════════════════════════
#   VTOP MARKS — QUERY
# ══════════════════════════════════════════════════════════

def get_marks_synced_sems() -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT semester_id FROM vtop_marks ORDER BY semester_id DESC"
    ).fetchall()
    conn.close()
    return [r["semester_id"] for r in rows]

def get_marks_fresh_enough(max_age_hours: int = 12, semester_id: str = None) -> bool:
    conn = get_db()
    if semester_id:
        row = conn.execute(
            "SELECT synced_at FROM vtop_sync_log WHERE data_type='marks' AND semester_id=?",
            (semester_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT MAX(synced_at) as synced_at FROM vtop_sync_log WHERE data_type='marks'"
        ).fetchone()
    conn.close()
    if not row or not row["synced_at"]:
        return False
    return datetime.now() - datetime.fromisoformat(row["synced_at"]) < timedelta(hours=max_age_hours)

def get_all_marks_summary(semester_id: str = None) -> str:
    """Full marks for one semester. Defaults to most recently synced."""
    conn = get_db()
    if not semester_id:
        row = conn.execute(
            "SELECT semester_id FROM vtop_sync_log WHERE data_type='marks' ORDER BY synced_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            conn.close()
            return ""
        semester_id = row["semester_id"]

    rows = conn.execute("""
        SELECT course_code, course_title, course_type, mark_title,
               scored_mark, max_mark, weightage_mark, weightage_percent, status
        FROM vtop_marks
        WHERE semester_id = ? AND mark_title != 'NO_MARKS_YET'
        ORDER BY course_code, mark_title
    """, (semester_id,)).fetchall()
    conn.close()

    if not rows:
        return ""

    label    = sem_label(semester_id)
    subjects = {}
    for r in rows:
        code = r["course_code"]
        if code not in subjects:
            ctype = r["course_type"] or course_type_label(code)
            subjects[code] = {"title": r["course_title"], "type": ctype, "marks": []}
        scored = r["scored_mark"]    if r["scored_mark"]    is not None else "N/A"
        max_m  = r["max_mark"]       if r["max_mark"]       is not None else "N/A"
        wt     = r["weightage_mark"] if r["weightage_mark"] is not None else ""
        wt_str = f" | Weightage: {wt}" if wt else ""
        subjects[code]["marks"].append(
            f"    {r['mark_title']}: {scored}/{max_m}{wt_str} ({r['status']})"
        )

    lines = [f"=== Marks — {label} ==="]
    for code, info in subjects.items():
        lines.append(f"\n{info['title']} ({code}) [{info['type']}]:")
        lines.extend(info["marks"])
    return "\n".join(lines)

def get_all_sems_marks_summary() -> str:
    """All marks across every synced semester."""
    conn = get_db()
    sem_ids = [r["semester_id"] for r in conn.execute(
        "SELECT DISTINCT semester_id FROM vtop_marks ORDER BY semester_id ASC"
    ).fetchall()]
    conn.close()
    if not sem_ids:
        return ""
    parts = [s for s in (get_all_marks_summary(sid) for sid in sem_ids) if s]
    return "\n\n".join(parts)

def get_subject_marks(subject_query: str, semester_id: str = None) -> str:
    """
    All marks for a subject matched by code or title (LIKE).
    If semester_id given, searches that sem only, else all sems.
    """
    conn = get_db()
    like = f"%{subject_query}%"

    if semester_id and semester_id != "all":
        rows = conn.execute("""
            SELECT course_code, course_title, course_type, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE semester_id = ? AND mark_title != 'NO_MARKS_YET'
              AND (LOWER(course_code) LIKE LOWER(?) OR LOWER(course_title) LIKE LOWER(?))
            ORDER BY mark_title
        """, (semester_id, like, like)).fetchall()
    else:
        rows = conn.execute("""
            SELECT course_code, course_title, course_type, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE mark_title != 'NO_MARKS_YET'
              AND (LOWER(course_code) LIKE LOWER(?) OR LOWER(course_title) LIKE LOWER(?))
            ORDER BY semester_id DESC, mark_title
        """, (like, like)).fetchall()
    conn.close()

    if not rows:
        return f"No marks found for '{subject_query}'."

    groups = {}
    for r in rows:
        key = (r["semester_id"], r["course_code"])
        if key not in groups:
            ctype = r["course_type"] or course_type_label(r["course_code"])
            groups[key] = {"title": r["course_title"], "code": r["course_code"],
                           "type": ctype, "sem": r["semester_id"], "marks": []}
        scored = r["scored_mark"]    if r["scored_mark"]    is not None else "N/A"
        max_m  = r["max_mark"]       if r["max_mark"]       is not None else "N/A"
        wt     = r["weightage_mark"] if r["weightage_mark"] is not None else ""
        wt_str = f" | Weightage: {wt}" if wt else ""
        groups[key]["marks"].append(
            f"    {r['mark_title']}: {scored}/{max_m}{wt_str} ({r['status']})"
        )

    lines = []
    for key, g in groups.items():
        lines.append(f"\n{g['title']} ({g['code']}) [{g['type']}] — {sem_label(g['sem'])}:")
        lines.extend(g["marks"])
    return "\n".join(lines).strip()

def get_specific_assessment(subject_query: str, assessment_query: str,
                             semester_id: str = None) -> str:
    """Single assessment for a subject across all or one semester."""
    aq = assessment_query.lower().strip()
    assess_search = {
        "cat1": "Continuous Assessment Test - I",
        "cat 1": "Continuous Assessment Test - I",
        "cat-1": "Continuous Assessment Test - I",
        "cat2": "Continuous Assessment Test - II",
        "cat 2": "Continuous Assessment Test - II",
        "cat-2": "Continuous Assessment Test - II",
        "fat": "Final Assessment Test",
        "final": "Final Assessment Test",
        "assignment 1": "Assignment - I",  "assignment1": "Assignment - I",
        "assignment-1": "Assignment - I",
        "assignment 2": "Assignment - II", "assignment2": "Assignment - II",
        "assignment-2": "Assignment - II",
        "assignment 3": "Assignment - III","assignment3": "Assignment - III",
        "assignment-3": "Assignment - III",
        "assessment 1": "Assessment - 1",  "assessment1": "Assessment - 1",
        "assessment 2": "Assessment - 2",  "assessment2": "Assessment - 2",
        "assessment 3": "Assessment - 3",  "assessment3": "Assessment - 3",
    }.get(aq, assessment_query)

    conn        = get_db()
    like_subj   = f"%{subject_query}%"
    like_assess = f"%{assess_search}%"

    if semester_id and semester_id != "all":
        rows = conn.execute("""
            SELECT course_code, course_title, course_type, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE semester_id = ?
              AND (LOWER(course_code) LIKE LOWER(?) OR LOWER(course_title) LIKE LOWER(?))
              AND LOWER(mark_title) LIKE LOWER(?)
        """, (semester_id, like_subj, like_subj, like_assess)).fetchall()
    else:
        rows = conn.execute("""
            SELECT course_code, course_title, course_type, mark_title,
                   scored_mark, max_mark, weightage_mark, status, semester_id
            FROM vtop_marks
            WHERE (LOWER(course_code) LIKE LOWER(?) OR LOWER(course_title) LIKE LOWER(?))
              AND LOWER(mark_title) LIKE LOWER(?)
            ORDER BY semester_id DESC
        """, (like_subj, like_subj, like_assess)).fetchall()
    conn.close()

    if not rows:
        return f"No '{assessment_query}' found for '{subject_query}'."

    lines = []
    for r in rows:
        scored = r["scored_mark"]    if r["scored_mark"]    is not None else "N/A"
        max_m  = r["max_mark"]       if r["max_mark"]       is not None else "N/A"
        wt     = r["weightage_mark"] if r["weightage_mark"] is not None else ""
        wt_str = f" | Weightage: {wt}" if wt else ""
        ctype  = r["course_type"] or course_type_label(r["course_code"])
        lines.append(
            f"{r['course_title']} ({r['course_code']}) [{ctype}] — {sem_label(r['semester_id'])}:\n"
            f"    {r['mark_title']}: {scored}/{max_m}{wt_str} ({r['status']})"
        )
    return "\n".join(lines)

def get_available_subjects() -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT course_code, course_title, semester_id FROM vtop_marks ORDER BY semester_id ASC, course_code"
    ).fetchall()
    conn.close()
    return [(r["course_code"], r["course_title"], r["semester_id"]) for r in rows]

def get_subject_search_hint() -> str:
    """
    Subject list injected into Groq so it can match any abbreviation or shorthand.
    Includes course type (Lab/Practical/Theory) and semester.
    Groq uses this to understand dmgt=Discrete Mathematics, toc=Theory of Computation etc.
    """
    subjects = get_available_subjects()
    if not subjects:
        return ""
    lines = ["Subjects across all semesters:"]
    current_sem = None
    for code, title, sem_id in subjects:
        if sem_id != current_sem:
            lines.append(f"\n  [{sem_label(sem_id)}]")
            current_sem = sem_id
        ctype = course_type_label(code)
        lines.append(f"    {code} — {title} [{ctype}]")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#   VTOP ATTENDANCE — SAVE + QUERY (pure upsert, one row per course)
# ══════════════════════════════════════════════════════════

def save_vtop_attendance(attendance_dict: dict, semester_id: str = None):
    """
    Save parsed attendance from vtop_handler into SQLite.
    attendance_dict: {slot: {attended, total, percentage, courseName,
                              code, ...}, ...} — see
    features/vtop_handler/parsers/parse_attendance.py for the exact shape.
    One permanent row per (course_code, semester_id) — a completed
    semester's attendance never changes once fetched, so every semester's
    data is kept forever (mirrors vtop_marks) rather than only ever
    holding a single current-semester snapshot. `semester_id` defaults to
    CURRENT_SEM so existing current-semester call sites don't need to
    change.
    """
    semester_id = semester_id or CURRENT_SEM
    conn  = get_db()
    now   = datetime.now().isoformat()
    count = 0

    def safe_int(v):
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None

    def safe_float(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    for slot, details in attendance_dict.items():
        course_code = (details.get("code") or "").strip()
        course_name = (details.get("courseName") or "").strip()
        if not course_code:
            continue
        conn.execute("""
            INSERT INTO vtop_attendance
                (course_code, course_name, slot, total_classes,
                 attended_classes, percentage, last_synced, semester_id)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(course_code, semester_id) DO UPDATE SET
                course_name      = excluded.course_name,
                slot             = excluded.slot,
                total_classes    = excluded.total_classes,
                attended_classes = excluded.attended_classes,
                percentage       = excluded.percentage,
                last_synced      = excluded.last_synced
        """, (
            course_code, course_name, slot,
            safe_int(details.get("total")),
            safe_int(details.get("attended")),
            safe_float(details.get("percentage")),
            now, semester_id
        ))
        count += 1

    conn.commit()
    conn.close()
    print(f"✅ Saved/updated {count} attendance rows for {sem_label(semester_id)}")

    sync_course_aliases([
        (d.get("code"), d.get("courseName")) for d in attendance_dict.values()
    ])


def get_attendance_fresh_enough(max_age_hours: int = 6, semester_id: str = None) -> bool:
    semester_id = semester_id or CURRENT_SEM
    conn = get_db()
    row  = conn.execute(
        "SELECT MAX(last_synced) as ts FROM vtop_attendance WHERE semester_id = ?",
        (semester_id,)
    ).fetchone()
    conn.close()
    if not row or not row["ts"]:
        return False
    return datetime.now() - datetime.fromisoformat(row["ts"]) < timedelta(hours=max_age_hours)


def get_all_attendance(semester_id: str = None) -> list:
    """All courses for one semester (default: current), worst attendance first."""
    semester_id = semester_id or CURRENT_SEM
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM vtop_attendance WHERE semester_id = ? ORDER BY percentage ASC",
        (semester_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_attendance_for_course(course_query: str, semester_id: str = None) -> list:
    semester_id = semester_id or CURRENT_SEM
    conn = get_db()
    like = f"%{course_query}%"
    rows = conn.execute("""
        SELECT * FROM vtop_attendance
        WHERE semester_id = ? AND (LOWER(course_code) LIKE LOWER(?) OR LOWER(course_name) LIKE LOWER(?))
        ORDER BY percentage ASC
    """, (semester_id, like, like)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def has_attendance_for_semester(semester_id: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM vtop_attendance WHERE semester_id = ? LIMIT 1", (semester_id,)
    ).fetchone()
    conn.close()
    return row is not None


# ══════════════════════════════════════════════════════════
#   VTOP TIMETABLE — SAVE + QUERY (wipe + re-insert, small dataset)
# ══════════════════════════════════════════════════════════

def save_vtop_timetable(timetable_dict: dict):
    """
    Save parsed timetable from vtop_handler into SQLite.
    timetable_dict: {"Monday": [{slot, courseName, code, class,
                                  startTime, endTime}, ...], ...} — see
    features/vtop_handler/parsers/time_table_parser.py for the exact shape.
    Wipes and re-inserts every sync — this is a small, semester-static
    dataset, so there's no benefit to upserting row by row.

    Note: the underlying scraper doesn't extract faculty names for the
    timetable view (unlike marks/attendance), so the `faculty` column
    is always left NULL here.
    """
    conn = get_db()
    now  = datetime.now().isoformat()

    conn.execute("DELETE FROM vtop_timetable")

    count = 0
    for day, classes in timetable_dict.items():
        for c in classes:
            conn.execute("""
                INSERT INTO vtop_timetable
                    (day, start_time, end_time, course_code, course_name,
                     room, slot, faculty, credits, synced_at, semester_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                day, c.get("startTime"), c.get("endTime"),
                c.get("code"), c.get("courseName"), c.get("class"),
                c.get("slot"), None, c.get("credits"), now, CURRENT_SEM
            ))
            count += 1

    conn.execute("""
        INSERT INTO vtop_sync_log (data_type, semester_id, synced_at)
        VALUES ('timetable', 'current', ?)
        ON CONFLICT(data_type, semester_id) DO UPDATE SET synced_at = excluded.synced_at
    """, (now,))

    conn.commit()
    conn.close()
    print(f"✅ Saved {count} timetable entries")

    sync_course_aliases([
        (c.get("code"), c.get("courseName"))
        for classes in timetable_dict.values() for c in classes
    ])


def get_timetable_fresh_enough(max_age_hours: int = 24) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT synced_at FROM vtop_sync_log WHERE data_type='timetable' AND semester_id='current'"
    ).fetchone()
    conn.close()
    if not row or not row["synced_at"]:
        return False
    return datetime.now() - datetime.fromisoformat(row["synced_at"]) < timedelta(hours=max_age_hours)


def get_timetable_for_day(day: str) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM vtop_timetable WHERE day = ?", (day,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_current_sem_course_credits() -> dict:
    """
    {course_code: credits} for every course in the current timetable
    (i.e. this semester's registered courses), skipping any course
    whose credits couldn't be extracted (see time_table_parser.py —
    the exact VTOP column name for credits wasn't verifiable without a
    live session). Returns {} if none were found at all.
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT course_code, credits FROM vtop_timetable
        WHERE credits IS NOT NULL
    """).fetchall()
    conn.close()
    return {r["course_code"]: r["credits"] for r in rows if r["course_code"]}


def get_full_timetable() -> dict:
    """{day: [class dict, ...], ...} for every day that has classes."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM vtop_timetable").fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["day"], []).append(dict(r))
    return result


# ══════════════════════════════════════════════════════════
#   VTOP EXAMS — SAVE + QUERY (wipe + re-insert, small dataset)
# ══════════════════════════════════════════════════════════

def save_vtop_exams(exam_dict: dict, semester_id: str = None):
    """
    Save parsed exam schedule from vtop_handler into SQLite.
    exam_dict: {exam_type_label: [{Course Code, Course Title, Exam Date,
                Exam Time, Venue Block, Venue Room, Seat No, Seat
                Location, ...}, ...], ...} — see
    features/vtop_handler/parsers/parse_exam_schedule.py for the exact
    shape. One permanent row per (course_code, exam_type, semester_id) —
    a completed semester's exam schedule never changes once it's synced,
    so every semester's is kept forever (mirrors vtop_marks/the
    per-semester vtop_attendance design) rather than the old wipe-every-
    sync approach, which meant a past semester's exam dates vanished the
    moment the current semester's schedule got synced. `semester_id`
    defaults to CURRENT_SEM so existing current-semester call sites
    don't need to change.

    Note: `exam_type_label` is whatever raw heading VTOP's table uses
    for each exam block (e.g. a CAT1/CAT2/FAT-style label) — it isn't
    normalized here, so filtering by "cat1"/"fat" downstream is
    best-effort string matching, not an exact enum.
    """
    semester_id = semester_id or CURRENT_SEM
    conn = get_db()
    now  = datetime.now().isoformat()

    count = 0
    for exam_type, rows in exam_dict.items():
        for row in rows:
            venue_block = row.get("Venue Block")
            venue_room  = row.get("Venue Room")
            venue       = "-".join([v for v in (venue_block, venue_room) if v]) or None
            seat_number = row.get("Seat No") or row.get("Seat Location")

            conn.execute("""
                INSERT INTO vtop_exams
                    (exam_type, course_code, course_name, exam_date,
                     session, venue, seat_number, synced_at, semester_id)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(course_code, exam_type, semester_id) DO UPDATE SET
                    course_name = excluded.course_name,
                    exam_date   = excluded.exam_date,
                    session     = excluded.session,
                    venue       = excluded.venue,
                    seat_number = excluded.seat_number,
                    synced_at   = excluded.synced_at
            """, (
                exam_type, row.get("Course Code"), row.get("Course Title"),
                row.get("Exam Date"), row.get("Exam Time"), venue,
                seat_number, now, semester_id
            ))
            count += 1

    conn.execute("""
        INSERT INTO vtop_sync_log (data_type, semester_id, synced_at)
        VALUES ('exams', 'current', ?)
        ON CONFLICT(data_type, semester_id) DO UPDATE SET synced_at = excluded.synced_at
    """, (now,))

    conn.commit()
    conn.close()
    print(f"✅ Saved {count} exam rows for {sem_label(semester_id)}")


def get_exams_fresh_enough(max_age_hours: int = 24) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT synced_at FROM vtop_sync_log WHERE data_type='exams' AND semester_id='current'"
    ).fetchone()
    conn.close()
    if not row or not row["synced_at"]:
        return False
    return datetime.now() - datetime.fromisoformat(row["synced_at"]) < timedelta(hours=max_age_hours)


def has_exams_for_semester(semester_id: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM vtop_exams WHERE semester_id = ? LIMIT 1", (semester_id,)).fetchone()
    conn.close()
    return row is not None


def get_all_exams(semester_id: str = None) -> list:
    """All exam rows for one semester (default: current)."""
    semester_id = semester_id or CURRENT_SEM
    conn = get_db()
    rows = conn.execute("SELECT * FROM vtop_exams WHERE semester_id = ?", (semester_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
#   VTOP GRADES / CGPA — SAVE + QUERY
# ══════════════════════════════════════════════════════════

def save_vtop_grades(acad_history: dict):
    """
    Save parsed academic history from vtop_handler into SQLite.
    acad_history: {"summary": {"CGPA":..., "CreditsRegistered":...,
    "CreditsEarned":..., ...grade-letter counts...}, "subjects":
    {course_title: grade_letter, ...}, "courses": [{"code":...,
    "title":..., "credits":..., "grade":...}, ...]} — see
    features/vtop_handler/parsers/parse_acadhistory.py.

    "courses" gives real per-course code/credits/grade straight from
    VTOP (only for COMPLETED/graded courses — the current in-progress
    semester won't appear here yet, which is why the CGPA predictor
    still sources current-sem credits from the timetable instead, see
    get_current_sem_course_credits()). semester_id isn't given
    per-course here, so it's backfilled via a best-effort join against
    vtop_marks (matching on course_code). Falls back to the older
    title-only matching if "courses" is empty/missing.
    """
    import json
    conn = get_db()
    now  = datetime.now().isoformat()

    summary  = acad_history.get("summary", {}) or {}
    courses  = acad_history.get("courses") or []
    subjects = acad_history.get("subjects", {}) or {}

    known_keys = {"CGPA", "CreditsRegistered", "CreditsEarned"}
    extra = {k: v for k, v in summary.items() if k not in known_keys}

    conn.execute("""
        INSERT INTO vtop_cgpa_summary
            (cgpa, credits_registered, credits_earned, extra_json, synced_at)
        VALUES (?,?,?,?,?)
    """, (
        summary.get("CGPA"), summary.get("CreditsRegistered"),
        summary.get("CreditsEarned"), json.dumps(extra), now
    ))

    count = 0

    if courses:
        for c in courses:
            course_title = (c.get("title") or "").strip()
            course_code  = (c.get("code") or "").strip() or None
            grade        = c.get("grade")
            try:
                credits = float(c.get("credits")) if c.get("credits") is not None else None
            except (ValueError, TypeError):
                credits = None
            if not course_title:
                continue

            semester_id = None
            if course_code:
                match = conn.execute("""
                    SELECT semester_id FROM vtop_marks
                    WHERE course_code = ? ORDER BY semester_id DESC LIMIT 1
                """, (course_code,)).fetchone()
                semester_id = match["semester_id"] if match else None

            conn.execute("""
                INSERT INTO vtop_grades
                    (semester_id, course_code, course_name, credits, grade, synced_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(course_name) DO UPDATE SET
                    semester_id = excluded.semester_id,
                    course_code = excluded.course_code,
                    credits     = excluded.credits,
                    grade       = excluded.grade,
                    synced_at   = excluded.synced_at
            """, (semester_id, course_code, course_title, credits, grade, now))
            count += 1
    else:
        for course_title, grade in subjects.items():
            course_title = (course_title or "").strip()
            if not course_title:
                continue

            match = conn.execute("""
                SELECT semester_id, course_code FROM vtop_marks
                WHERE LOWER(course_title) = LOWER(?)
                ORDER BY semester_id DESC LIMIT 1
            """, (course_title,)).fetchone()
            semester_id = match["semester_id"] if match else None
            course_code = match["course_code"] if match else None

            conn.execute("""
                INSERT INTO vtop_grades
                    (semester_id, course_code, course_name, grade, synced_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(course_name) DO UPDATE SET
                    semester_id = excluded.semester_id,
                    course_code = excluded.course_code,
                    grade       = excluded.grade,
                    synced_at   = excluded.synced_at
            """, (semester_id, course_code, course_title, grade, now))
            count += 1

    conn.execute("""
        INSERT INTO vtop_sync_log (data_type, semester_id, synced_at)
        VALUES ('grades', 'current', ?)
        ON CONFLICT(data_type, semester_id) DO UPDATE SET synced_at = excluded.synced_at
    """, (now,))

    conn.commit()
    conn.close()
    print(f"✅ Saved/updated {count} grade rows")


def get_grades_fresh_enough(max_age_hours: int = 24) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT synced_at FROM vtop_sync_log WHERE data_type='grades' AND semester_id='current'"
    ).fetchone()
    conn.close()
    if not row or not row["synced_at"]:
        return False
    return datetime.now() - datetime.fromisoformat(row["synced_at"]) < timedelta(hours=max_age_hours)


def get_latest_cgpa_summary() -> dict | None:
    conn = get_db()
    row  = conn.execute("SELECT * FROM vtop_cgpa_summary ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_grades() -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM vtop_grades ORDER BY semester_id ASC, course_name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_grades_for_semester(semester_id: str) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM vtop_grades WHERE semester_id = ? ORDER BY course_name", (semester_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_grade_for_course(course_query: str) -> list:
    conn = get_db()
    like = f"%{course_query}%"
    rows = conn.execute("""
        SELECT * FROM vtop_grades
        WHERE LOWER(course_name) LIKE LOWER(?) OR LOWER(course_code) LIKE LOWER(?)
        ORDER BY semester_id DESC
    """, (like, like)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
#   LONG-TERM MEMORY (SQLite FTS5)
# ══════════════════════════════════════════════════════════
# Replaced ChromaDB + its ONNX embedding model (~70 MB resident, several
# seconds to load) — too heavy for a process meant to idle all day.
# Facts are few, so all of them are handed to the model (nothing can be
# missed); past conversations are searched with SQLite's built-in
# full-text index and only used when they share real content words with
# the new question.

_STOPWORDS = {
    "the", "and", "for", "are", "was", "what", "whats", "how", "why", "who", "when", "where", "which",
    "you", "your", "yours", "me", "my", "mine", "can", "could", "would", "should", "will", "just", "about",
    "this", "that", "these", "those", "with", "from", "have", "has", "had", "does", "did", "not", "any",
    "tell", "give", "please", "jarvis", "boss", "its", "it's", "is", "am", "be", "been", "there", "then",
    "some", "into", "also", "like", "get", "got", "want", "need", "know", "make", "let", "one", "all",
}
MAX_FACTS_IN_CONTEXT = 25


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) >= 3 and w not in _STOPWORDS}


def _init_memory_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS remembered_facts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            fact       TEXT NOT NULL,
            category   TEXT DEFAULT 'general',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts
            USING fts5(user_msg, jarvis_msg, content='conversations', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS conversations_ai AFTER INSERT ON conversations BEGIN
            INSERT INTO conversations_fts(rowid, user_msg, jarvis_msg) VALUES (new.id, new.user_msg, new.jarvis_msg);
        END;
        CREATE TRIGGER IF NOT EXISTS conversations_ad AFTER DELETE ON conversations BEGIN
            INSERT INTO conversations_fts(conversations_fts, rowid, user_msg, jarvis_msg)
                VALUES ('delete', old.id, old.user_msg, old.jarvis_msg);
        END;
    """)
    # count(*) on an external-content FTS table reads the SOURCE table, so
    # it can't tell whether the index was ever built — use a one-time flag.
    built = conn.execute("SELECT value FROM preferences WHERE key='conversations_fts_v1'").fetchone()
    if not built:
        conn.execute("INSERT INTO conversations_fts(conversations_fts) VALUES ('rebuild')")
        conn.execute("INSERT OR REPLACE INTO preferences (key, value) VALUES ('conversations_fts_v1', '1')")
    conn.commit()


def remember_fact(fact: str, category: str = "general") -> str:
    conn = get_db()
    conn.execute("INSERT INTO remembered_facts (fact, category) VALUES (?, ?)", (fact.strip(), category))
    conn.commit()
    conn.close()
    return "Got it, I'll remember that."


def list_facts(limit: int = 50) -> list:
    conn = get_db()
    rows = conn.execute("SELECT fact FROM remembered_facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [r["fact"] for r in rows]


def recall_facts(query: str, n: int = MAX_FACTS_IN_CONTEXT) -> list:
    return list_facts(limit=n)


# ══════════════════════════════════════════════════════════
#   CONVERSATIONS
# ══════════════════════════════════════════════════════════

_TRIVIAL = {"ok","okay","thanks","thank you","got it","sure","yes",
            "no","alright","cool","nice","fine","bye","great","hmm","hm"}

def save_conversation(user_msg: str, jarvis_msg: str, topic: str = "general"):
    conn = get_db()
    conn.execute(
        "INSERT INTO conversations (user_msg, jarvis_msg, topic) VALUES (?,?,?)",
        (user_msg, jarvis_msg, topic)
    )
    conn.commit()
    conn.close()


def recall_conversations(query: str, n: int = 1) -> list:
    """A past exchange sharing at least two content words (and half of the
    question's content words) with this one — otherwise nothing."""
    words = _content_words(query)
    if len(words) < 2:
        return []
    match = " OR ".join(f'"{w}"' for w in sorted(words))
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT c.user_msg, c.jarvis_msg FROM conversations_fts f JOIN conversations c ON c.id = f.rowid "
            "WHERE conversations_fts MATCH ? ORDER BY bm25(conversations_fts) LIMIT 5", (match,)
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    out = []
    for r in rows:
        overlap = words & _content_words(r["user_msg"])
        if len(overlap) >= 2 and len(overlap) >= len(words) / 2 and len(r["user_msg"].split()) > 3:
            out.append(f"User: {r['user_msg']} | Jarvis: {r['jarvis_msg']}")
            if len(out) >= n:
                break
    return out


def build_context(user_input: str) -> str:
    parts = []
    facts = recall_facts(user_input)
    convs = recall_conversations(user_input, n=1)
    if facts:
        parts.append("Things the user asked you to remember: " + " | ".join(facts))
    if convs:
        parts.append("A related past exchange: " + convs[0][:200])
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════
#   PREFERENCES, TASKS, REMINDERS, EXPENSES
# ══════════════════════════════════════════════════════════

def set_preference(key: str, value):
    conn = get_db()
    conn.execute("""
        INSERT INTO preferences (key, value, updated_at)
        VALUES (?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """, (key, str(value)))
    conn.commit()
    conn.close()

def get_preference(key: str, default=None):
    conn = get_db()
    row  = conn.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def add_task(title: str, due_at: str = None, priority: str = "normal",
             tag: str = None, notes: str = None) -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO tasks (title, notes, due_at, priority, tag) VALUES (?,?,?,?,?)
    """, (title, notes, due_at, priority, tag))
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return task_id

def list_tasks(status: str = "open", due_within_days: int = None, tag: str = None) -> list:
    conn = get_db()
    query  = "SELECT * FROM tasks WHERE status = ?"
    params = [status]
    if tag:
        query += " AND tag = ?"
        params.append(tag)
    if due_within_days is not None:
        cutoff = (datetime.now() + timedelta(days=due_within_days)).isoformat()
        query += " AND due_at IS NOT NULL AND due_at <= ?"
        params.append(cutoff)
    query += " ORDER BY (due_at IS NULL), due_at ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_task(task_id: int) -> dict | None:
    conn = get_db()
    row  = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def search_tasks(query: str, status: str = "open") -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status = ? AND title LIKE ? ORDER BY (due_at IS NULL), due_at ASC",
        (status, f"%{query}%"),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def complete_task(task_id: int):
    conn = get_db()
    conn.execute(
        "UPDATE tasks SET status='done', completed_at=? WHERE id=?",
        (datetime.now().isoformat(), task_id),
    )
    conn.commit()
    conn.close()

def drop_task(task_id: int):
    conn = get_db()
    conn.execute("UPDATE tasks SET status='dropped' WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

def update_task(task_id: int, **fields):
    if not fields:
        return
    allowed = {"title", "notes", "due_at", "priority", "status", "tag"}
    sets    = [f"{k}=?" for k in fields if k in allowed]
    values  = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return
    conn = get_db()
    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*values, task_id))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════
#   SCHEDULE BLOCKS — "time on the clock", distinct from tasks
#   ("things to do"). Classes get materialized in here too
#   (block_type='class') so this table is the single source of
#   truth for "what am I doing right now".
# ══════════════════════════════════════════════════════════

def add_block(title: str, start_at: str, end_at: str, block_type: str = "custom",
              linked_course: str = None, linked_task_id: int = None, notes: str = None) -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO schedule_blocks (title, start_at, end_at, block_type, linked_course, linked_task_id, notes)
        VALUES (?,?,?,?,?,?,?)
    """, (title, start_at, end_at, block_type, linked_course, linked_task_id, notes))
    conn.commit()
    block_id = cur.lastrowid
    conn.close()
    return block_id

def blocks_for(target_date) -> list:
    """target_date: a date/datetime object or 'YYYY-MM-DD' string."""
    day_str = target_date if isinstance(target_date, str) else target_date.isoformat()
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM schedule_blocks
        WHERE date(start_at) = date(?) AND status != 'skipped'
        ORDER BY start_at ASC
    """, (day_str,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def blocks_between(start_dt, end_dt) -> list:
    start_s = start_dt if isinstance(start_dt, str) else start_dt.isoformat()
    end_s   = end_dt if isinstance(end_dt, str) else end_dt.isoformat()
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM schedule_blocks
        WHERE start_at < ? AND end_at > ? AND status != 'skipped'
        ORDER BY start_at ASC
    """, (end_s, start_s)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def active_block(now: datetime = None) -> dict | None:
    now = now or datetime.now()
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM schedule_blocks
        WHERE start_at <= ? AND end_at > ? AND status NOT IN ('skipped', 'completed')
        ORDER BY start_at DESC LIMIT 1
    """, (now.isoformat(), now.isoformat())).fetchone()
    conn.close()
    return dict(row) if row else None

def next_block(now: datetime = None) -> dict | None:
    now = now or datetime.now()
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM schedule_blocks
        WHERE start_at > ? AND status != 'skipped'
        ORDER BY start_at ASC LIMIT 1
    """, (now.isoformat(),)).fetchone()
    conn.close()
    return dict(row) if row else None

def mark_block_status(block_id: int, status: str):
    conn = get_db()
    conn.execute("UPDATE schedule_blocks SET status=? WHERE id=?", (status, block_id))
    conn.commit()
    conn.close()

def find_conflicts(start_at: str, end_at: str, exclude_id: int = None) -> list:
    conn = get_db()
    query  = """
        SELECT * FROM schedule_blocks
        WHERE start_at < ? AND end_at > ? AND status NOT IN ('skipped', 'completed')
    """
    params = [end_at, start_at]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_block(block_id: int):
    conn = get_db()
    conn.execute("DELETE FROM schedule_blocks WHERE id=?", (block_id,))
    conn.commit()
    conn.close()

def class_block_exists(course_code: str, start_at: str) -> bool:
    conn = get_db()
    row = conn.execute("""
        SELECT id FROM schedule_blocks WHERE block_type='class' AND linked_course=? AND start_at=?
    """, (course_code, start_at)).fetchone()
    conn.close()
    return row is not None

def materialize_classes_for_date(target_date) -> int:
    """
    Copies vtop_timetable rows for target_date's weekday into
    schedule_blocks as block_type='class'. Idempotent — skips any
    (course, start_at) pair that already exists, so it's safe to call
    on every startup and on a recurring timer.
    """
    day_name = target_date.strftime("%A")
    rows = get_timetable_for_day(day_name)
    count = 0
    for r in rows:
        if not r["start_time"] or not r["end_time"]:
            continue
        start_at = datetime.combine(target_date, datetime.strptime(r["start_time"], "%H:%M").time()).isoformat()
        end_at   = datetime.combine(target_date, datetime.strptime(r["end_time"], "%H:%M").time()).isoformat()
        if class_block_exists(r["course_code"], start_at):
            continue
        add_block(
            title=r["course_name"] or r["course_code"] or "Class",
            start_at=start_at, end_at=end_at, block_type="class",
            linked_course=r["course_code"],
        )
        count += 1
    return count



# ══════════════════════════════════════════════════════════
#   FOCUS SESSIONS — the execution engine for schedule blocks
# ══════════════════════════════════════════════════════════

def start_focus_session(subject: str, planned_minutes: int, linked_block_id: int = None) -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO focus_sessions (linked_block_id, subject, started_at, planned_minutes, status)
        VALUES (?,?,?,?, 'active')
    """, (linked_block_id, subject, datetime.now().isoformat(), planned_minutes))
    conn.commit()
    session_id = cur.lastrowid
    conn.close()
    return session_id

def get_active_focus_session() -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM focus_sessions WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def get_focus_session(session_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def end_focus_session(session_id: int, actual_minutes: int, pomodoros_completed: int, status: str = "completed"):
    conn = get_db()
    conn.execute("""
        UPDATE focus_sessions SET ended_at=?, actual_minutes=?, pomodoros_completed=?, status=?
        WHERE id=?
    """, (datetime.now().isoformat(), actual_minutes, pomodoros_completed, status, session_id))
    conn.commit()
    conn.close()

def increment_focus_pomodoro(session_id: int):
    conn = get_db()
    conn.execute(
        "UPDATE focus_sessions SET pomodoros_completed = pomodoros_completed + 1 WHERE id=?",
        (session_id,),
    )
    conn.commit()
    conn.close()

def get_focus_sessions_between(start_dt, end_dt) -> list:
    start_s = start_dt if isinstance(start_dt, str) else start_dt.isoformat()
    end_s   = end_dt if isinstance(end_dt, str) else end_dt.isoformat()
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM focus_sessions
        WHERE started_at >= ? AND started_at < ? AND status != 'active'
        ORDER BY started_at
    """, (start_s, end_s)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_focus_stats(days: int = 7) -> list:
    conn = get_db()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT subject, SUM(COALESCE(actual_minutes, 0)) AS total_minutes, COUNT(*) AS sessions
        FROM focus_sessions
        WHERE started_at >= ? AND subject IS NOT NULL AND status != 'active'
        GROUP BY subject
        ORDER BY total_minutes DESC
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_reminder(title: str, trigger_time: str, recurring: bool = False) -> str:
    conn = get_db()
    conn.execute("INSERT INTO reminders (title, trigger_time, recurring) VALUES (?,?,?)",
                 (title, trigger_time, 1 if recurring else 0))
    conn.commit()
    conn.close()
    return f"Reminder set: {title} at {trigger_time}"

def get_pending_reminders() -> list:
    conn = get_db()
    rows = conn.execute("SELECT * FROM reminders WHERE done=0 ORDER BY trigger_time ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_expense(amount: float, category: str = None, merchant: str = None, note: str = None,
                 source: str = "voice", raw_text: str = None, spent_at: str = None,
                 currency: str = "INR") -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO expenses (amount, currency, category, merchant, note, source, raw_text, spent_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (amount, currency, category, merchant, note, source, raw_text, spent_at or datetime.now().isoformat()))
    conn.commit()
    expense_id = cur.lastrowid
    conn.close()
    return expense_id

def get_expenses_between(start_dt, end_dt) -> list:
    start_s = start_dt if isinstance(start_dt, str) else start_dt.isoformat()
    end_s   = end_dt if isinstance(end_dt, str) else end_dt.isoformat()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM expenses WHERE spent_at >= ? AND spent_at < ? ORDER BY spent_at DESC",
        (start_s, end_s),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_expenses_by_category(start_dt, end_dt) -> list:
    start_s = start_dt if isinstance(start_dt, str) else start_dt.isoformat()
    end_s   = end_dt if isinstance(end_dt, str) else end_dt.isoformat()
    conn = get_db()
    rows = conn.execute("""
        SELECT COALESCE(category, 'other') AS category, SUM(amount) AS total, COUNT(*) AS count
        FROM expenses WHERE spent_at >= ? AND spent_at < ?
        GROUP BY category ORDER BY total DESC
    """, (start_s, end_s)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def search_expenses(query: str, start_dt=None, end_dt=None) -> list:
    conn = get_db()
    sql    = "SELECT * FROM expenses WHERE (merchant LIKE ? OR note LIKE ? OR raw_text LIKE ?)"
    params = [f"%{query}%", f"%{query}%", f"%{query}%"]
    if start_dt is not None:
        sql += " AND spent_at >= ?"
        params.append(start_dt if isinstance(start_dt, str) else start_dt.isoformat())
    if end_dt is not None:
        sql += " AND spent_at < ?"
        params.append(end_dt if isinstance(end_dt, str) else end_dt.isoformat())
    sql += " ORDER BY spent_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def find_recent_duplicate_expense(amount: float, merchant: str, spent_at: str, window_minutes: int = 10) -> dict | None:
    """Used by the WhatsApp UPI ingest path — a transaction can hit both SMS
    and WhatsApp, and the daily brief may re-surface it; skip a near-exact repeat."""
    target = datetime.fromisoformat(spent_at)
    window_start = (target - timedelta(minutes=window_minutes)).isoformat()
    window_end   = (target + timedelta(minutes=window_minutes)).isoformat()
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM expenses
        WHERE amount = ? AND COALESCE(merchant,'') = ? AND spent_at BETWEEN ? AND ?
        LIMIT 1
    """, (amount, merchant or "", window_start, window_end)).fetchone()
    conn.close()
    return dict(row) if row else None


# ══════════════════════════════════════════════════════════
#   MERCHANT ALIASES (V.3c) — teaching Jarvis a friendly name for an
#   opaque UPI VPA ("call P2A-ABC123 the tea guy"), so future ingests
#   substitute it instead of showing a meaningless raw handle forever.
# ══════════════════════════════════════════════════════════

def get_merchant_alias(vpa: str) -> str | None:
    if not vpa:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT friendly_name FROM merchant_aliases WHERE vpa = ?", (vpa,)
    ).fetchone()
    conn.close()
    return row["friendly_name"] if row else None


def add_merchant_alias(vpa: str, friendly_name: str):
    conn = get_db()
    conn.execute("""
        INSERT INTO merchant_aliases (vpa, friendly_name) VALUES (?, ?)
        ON CONFLICT(vpa) DO UPDATE SET friendly_name = excluded.friendly_name
    """, (vpa, friendly_name))
    conn.commit()
    conn.close()


def get_last_opaque_expense() -> dict | None:
    """Most recent expense whose merchant looks like a raw VPA/handle
    (contains '@' or isn't a normal-looking name) and doesn't already
    have an alias — what 'that opaque VPA is the tea guy' refers to."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM expenses
        WHERE merchant IS NOT NULL AND merchant LIKE '%@%'
        ORDER BY logged_at DESC LIMIT 10
    """).fetchall()
    conn.close()
    for row in rows:
        d = dict(row)
        if not get_merchant_alias(d["merchant"]):
            return d
    return None

def save_lms_assignments(assignments: list):
    """
    Save/update assignments from LMS.
    Returns list of NEW assignments (for proactive announcement).
    Pure upsert — never deletes.
    """
    from datetime import datetime
    conn = get_db()
    now  = datetime.now().isoformat()
    new_assignments = []
 
    for a in assignments:
        # Check if already exists
        existing = conn.execute(
            "SELECT id, status FROM lms_assignments WHERE title=? AND course_name=?",
            (a["title"], a["course_name"])
        ).fetchone()
 
        if not existing:
            new_assignments.append(a)
            conn.execute("""
                INSERT INTO lms_assignments
                    (title, course_name, course_url, due_date, due_date_str,
                     assign_url, description, status, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                a["title"], a["course_name"], a.get("course_url",""),
                a["due_date"].isoformat() if hasattr(a["due_date"], "isoformat") else a["due_date"],
                a.get("due_date_str",""), a.get("assign_url",""),
                a.get("description",""), a.get("status","not_submitted"), now
            ))
        else:
            # Update status and due date (could change) — status was
            # missing from this SET clause entirely, so a fresh
            # "submitted" scraped on re-sync (see lms.py's _sync_once,
            # which re-checks the live assignment page every time) was
            # silently discarded and the row stayed frozen at whatever
            # status it had on its very first insert.
            conn.execute("""
                UPDATE lms_assignments
                SET due_date=?, due_date_str=?, assign_url=?,
                    description=?, status=?, synced_at=?
                WHERE title=? AND course_name=?
            """, (
                a["due_date"].isoformat() if hasattr(a["due_date"], "isoformat") else a["due_date"],
                a.get("due_date_str",""), a.get("assign_url",""),
                a.get("description",""), a.get("status", existing["status"]), now,
                a["title"], a["course_name"]
            ))
 
    conn.execute("INSERT INTO lms_sync_log (synced_at) VALUES (?)", (now,))
    conn.commit()
    conn.close()
    return new_assignments
 
 
def update_lms_status(title: str, course_name: str, status: str):
    conn = get_db()
    conn.execute("""
        UPDATE lms_assignments SET status=? WHERE title=? AND course_name=?
    """, (status, title, course_name))
    conn.commit()
    conn.close()
 
 
def get_pending_lms_assignments() -> list:
    """Returns all not-submitted assignments ordered by due date."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM lms_assignments
        WHERE status != 'submitted'
        ORDER BY due_date ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
 
 
def get_all_lms_assignments() -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM lms_assignments ORDER BY due_date ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
 
 
def mark_lms_reminded(assignment_id: int, tier: str):
    """tier: '3day' or '1day'"""
    col  = f"reminded_{tier}"
    conn = get_db()
    conn.execute(f"UPDATE lms_assignments SET {col}=1 WHERE id=?", (assignment_id,))
    conn.commit()
    conn.close()
 
 
def get_lms_last_sync() -> str | None:
    conn = get_db()
    row  = conn.execute(
        "SELECT synced_at FROM lms_sync_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row["synced_at"] if row else None


# ── Init on import ─────────────────────────────────────
init_db()

_mem_conn = get_db()
_init_memory_tables(_mem_conn)
_mem_conn.close()
