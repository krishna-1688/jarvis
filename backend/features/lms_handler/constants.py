"""
lms_handler/constants.py
"""
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
LMS_BASE_URL    = "https://lms.vit.ac.in"
LMS_LOGIN_URL   = f"{LMS_BASE_URL}/login/index.php"
LMS_CALENDAR_URL = f"{LMS_BASE_URL}/calendar/view.php?view=upcoming"
LMS_DASHBOARD_URL = f"{LMS_BASE_URL}/my/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}