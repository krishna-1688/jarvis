"""
Full LMS integration test.
Run after adding LMS_USERNAME, LMS_PASSWORD, MY_WHATSAPP_NUMBER to .env/config.py
"""
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from features.lms import fetch_and_sync_lms, format_assignments_response
from core.memory import get_pending_lms_assignments, get_all_lms_assignments

print("🔄 Syncing LMS...")
result = fetch_and_sync_lms()

if "error" in result:
    print("❌ Error:", result["error"])
else:
    print(f"✅ Found {len(result['assignments'])} assignments")
    print(f"   New this sync: {len(result['new'])}")

    if result["new"]:
        print("\n🆕 NEW ASSIGNMENTS:")
        for a in result["new"]:
            print(f"  - {a['title']} | {a['course_name']} | due {a['due_date_str']}")

print("\n📋 All pending assignments in DB:")
pending = get_pending_lms_assignments()
print(format_assignments_response(pending))

print("\n📋 All assignments in DB (including submitted):")
all_a = get_all_lms_assignments()
for a in all_a:
    print(f"  {a['title']} | {a['course_name']} | {a['status']} | due {a['due_date_str']}")