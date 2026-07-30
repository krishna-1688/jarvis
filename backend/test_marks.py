import sys, os, asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.memory import get_db
from features.vtop import fetch_vtop_all_sems
from core.memory import get_all_marks_summary

# Wipe all existing marks
conn = get_db()
conn.execute("DELETE FROM vtop_marks")
conn.execute("DELETE FROM vtop_sync_log WHERE data_type='marks'")
conn.commit()
conn.close()
print("🗑️  All marks cleared")

# Fresh fetch from VTOP
print("📡 Fetching all semesters from VTOP...")
result = fetch_vtop_all_sems()
print("Result:", result)

# Show summary
print("\n--- Marks Summary ---")
print(get_all_marks_summary())