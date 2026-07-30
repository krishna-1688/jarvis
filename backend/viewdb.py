import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.memory import get_db, SEM_LABELS

conn = get_db()

sems = conn.execute(
    "SELECT DISTINCT semester_id FROM vtop_marks ORDER BY semester_id ASC"
).fetchall()

for sem in sems:
    sem_id = sem["semester_id"]
    label = SEM_LABELS.get(sem_id, sem_id)
    print(f"\n{'='*60}")
    print(f"  {label} ({sem_id})")
    print(f"{'='*60}")

    subjects = conn.execute("""
        SELECT DISTINCT course_code, course_title, course_type
        FROM vtop_marks WHERE semester_id = ?
        ORDER BY course_code
    """, (sem_id,)).fetchall()

    for subj in subjects:
        print(f"\n  {subj['course_title']} ({subj['course_code']}) [{subj['course_type']}]")
        marks = conn.execute("""
            SELECT mark_title, scored_mark, max_mark, weightage_mark, status
            FROM vtop_marks
            WHERE semester_id = ? AND course_code = ?
            ORDER BY mark_title
        """, (sem_id, subj["course_code"])).fetchall()
        for m in marks:
            print(f"    {m['mark_title']}: {m['scored_mark']}/{m['max_mark']} (Weightage: {m['weightage_mark']}) [{m['status']}]")

conn.close()