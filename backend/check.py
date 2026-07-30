import sqlite3
conn = sqlite3.connect("data/database/jarvis.db")

# Check what semester chemistry is in
rows = conn.execute(
    "SELECT DISTINCT semester_id, course_code, course_title FROM vtop_marks "
    "WHERE LOWER(course_title) LIKE '%chem%'"
).fetchall()
print("Chemistry semesters:")
for r in rows: print(r)

# Check what semester_id the sync log thinks is latest
rows2 = conn.execute(
    "SELECT * FROM vtop_sync_log ORDER BY synced_at DESC"
).fetchall()
print("\nSync log:")
for r in rows2: print(tuple(r))

conn.close()