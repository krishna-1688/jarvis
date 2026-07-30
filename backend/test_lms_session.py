import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import LMS_USERNAME, LMS_PASSWORD
from features.lms_handler.session_generator import get_valid_session

session, status = get_valid_session(LMS_USERNAME, LMS_PASSWORD)

print("Status:", status)
print("Logged in:", session is not None)

if session:
    resp = session.get("https://lms.vit.ac.in/calendar/view.php?view=upcoming")
    print("Calendar fetch status code:", resp.status_code)