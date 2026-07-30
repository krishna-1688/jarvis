import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import urllib3
urllib3.disable_warnings()
from config import LMS_USERNAME, LMS_PASSWORD
from features.lms_handler.session_generator import get_valid_session

session, status = get_valid_session(LMS_USERNAME, LMS_PASSWORD)
print("Login status:", status)

if session:
    # Calendar upcoming
    resp = session.get("https://lms.vit.ac.in/calendar/view.php?view=upcoming")
    with open("lms_calendar.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("Saved lms_calendar.html")

    # Dashboard
    resp = session.get("https://lms.vit.ac.in/my/")
    with open("lms_dashboard.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("Saved lms_dashboard.html")

    # Also try timeline view which shows assignments differently
    resp = session.get("https://lms.vit.ac.in/my/index.php?myoverviewtab=timeline")
    with open("lms_timeline.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("Saved lms_timeline.html")
else:
    print("Login failed")