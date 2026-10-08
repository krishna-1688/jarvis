from core.semesters import campus as _campus, CURRENT_SEM, SEM_IDS  # noqa: E402,F401

# VTOP host for the campus in profile.toml (Chennai: vtopcc.vit.ac.in).
_HOST = _campus()["host"]

VTOP_BASE_URL = f"https://{_HOST}/vtop/open/page"
VTOP_PRE_LOGIN = f"https://{_HOST}/vtop/prelogin/setup"
VTOP_LOGIN_PAGE_REDIRECT = f"https://{_HOST}/vtop/init/page"
VTOP_LOGIN_URL = f"https://{_HOST}/vtop/login"
VTOP_DO_LOGIN_URL = f"https://{_HOST}/vtop/doLogin"

VTOP_ATTENDANCE_URL = f"https://{_HOST}/vtop/processViewStudentAttendance"
VTOP_SINGLE_SUBJECT_ATTENDANCE_URL = f"https://{_HOST}/vtop/processViewAttendanceDetail"
VTOP_TIMETABLE_URL = f"https://{_HOST}/vtop/processViewTimeTable"
VTOP_ACADHISTORY_URL = f"https://{_HOST}/vtop/examinations/examGradeView/StudentGradeHistory"
VTOP_PROFILE_URL = f"https://{_HOST}/vtop/studentsRecord/StudentProfileAllView"
VTOP_MARKS_URL = f"https://{_HOST}/vtop/examinations/doStudentMarkView"
VTOP_EXAM_SCHEDULE_URL = f"https://{_HOST}/vtop/examinations/doSearchExamScheduleForStudent"
VTOP_FACULTY_URL = r"https://vit.ac.in/faculty/"
VTOP_ACAD_CALENDER_URL = r"https://vit.ac.in/academic-calendar/"

COURSE_PAGE_URL = f"https://{_HOST}/vtop/academics/common/StudentCoursePage"
COURSE_PAGE_SEMESTER_URL = f"https://{_HOST}/vtop/getCourseForCoursePage"
COURSE_PAGE_SELECT_COURSE_URL = f"https://{_HOST}/vtop/getSlotIdForCoursePage"
COURSE_PAGE_GET_CONTENT_URL = f"https://{_HOST}/vtop/processViewStudentCourseDetail"

MARKS_VIEW_PAGE = f"https://{_HOST}/vtop/examinations/doStudentMarkView"
CURRICULUM_PAGE_URL = f"https://{_HOST}/vtop/academics/common/Curriculum"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36",
}

# CURRENT_SEM / SEM_IDS are computed per student in core/semesters.py
# (admission year + today's date), imported above.
