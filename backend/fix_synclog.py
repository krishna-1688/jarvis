import sqlite3
from datetime import datetime

conn = sqlite3.connect("data/database/jarvis.db")

# Step 1: Drop old sync log table and recreate with correct schema
conn.execute("DROP TABLE IF EXISTS vtop_sync_log")
conn.execute("""
    CREATE TABLE vtop_sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_type TEXT NOT NULL,
        semester_id TEXT NOT NULL,
        synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(data_type, semester_id)
    )
""")
conn.commit()
print("✅ Recreated vtop_sync_log with correct schema")

# Step 2: Insert a sync entry for every semester already in vtop_marks
sems = conn.execute("SELECT DISTINCT semester_id FROM vtop_marks").fetchall()
now = datetime.now().isoformat()
for row in sems:
    conn.execute(
        "INSERT INTO vtop_sync_log (data_type, semester_id, synced_at) VALUES ('marks', ?, ?)",
        (row[0], now)
    )

conn.commit()
print("\nFixed sync log:")
for r in conn.execute("SELECT * FROM vtop_sync_log").fetchall():
    print(tuple(r))

conn.close()
print("\n✅ Done. Now restart Jarvis and try asking about chemistry.")