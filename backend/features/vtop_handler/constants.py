VTOP_BASE_URL = r"https://vtopcc.vit.ac.in/vtop/open/page"
VTOP_PRE_LOGIN = r"https://vtopcc.vit.ac.in/vtop/prelogin/setup"
VTOP_LOGIN_PAGE_REDIRECT = r"https://vtopcc.vit.ac.in/vtop/init/page"
VTOP_LOGIN_URL = r"https://vtopcc.vit.ac.in/vtop/login"
VTOP_DO_LOGIN_URL = r"https://vtopcc.vit.ac.in/vtop/doLogin"

VTOP_ATTENDANCE_URL = r"https://vtopcc.vit.ac.in/vtop/processViewStudentAttendance"
VTOP_SINGLE_SUBJECT_ATTENDANCE_URL = r"https://vtopcc.vit.ac.in/vtop/processViewAttendanceDetail"
VTOP_TIMETABLE_URL = r"https://vtopcc.vit.ac.in/vtop/processViewTimeTable"
VTOP_ACADHISTORY_URL = r"https://vtopcc.vit.ac.in/vtop/examinations/examGradeView/StudentGradeHistory"
VTOP_PROFILE_URL = r"https://vtopcc.vit.ac.in/vtop/studentsRecord/StudentProfileAllView"
VTOP_MARKS_URL = r"https://vtopcc.vit.ac.in/vtop/examinations/doStudentMarkView"
VTOP_EXAM_SCHEDULE_URL = r"https://vtopcc.vit.ac.in/vtop/examinations/doSearchExamScheduleForStudent"
VTOP_FACULTY_URL = r"https://vit.ac.in/faculty/"
VTOP_ACAD_CALENDER_URL = r"https://vit.ac.in/academic-calendar/"

COURSE_PAGE_URL = r"https://vtopcc.vit.ac.in/vtop/academics/common/StudentCoursePage"
COURSE_PAGE_SEMESTER_URL = r"https://vtopcc.vit.ac.in/vtop/getCourseForCoursePage"
COURSE_PAGE_SELECT_COURSE_URL = r"https://vtopcc.vit.ac.in/vtop/getSlotIdForCoursePage"
COURSE_PAGE_GET_CONTENT_URL = r"https://vtopcc.vit.ac.in/vtop/processViewStudentCourseDetail"

MARKS_VIEW_PAGE = r"https://vtopcc.vit.ac.in/vtop/examinations/doStudentMarkView"
CURRICULUM_PAGE_URL = r"https://vtopcc.vit.ac.in/vtop/academics/common/Curriculum"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36",
}

# Current active semester — update each sem
CURRENT_SEM = "CH20262701"  # Fall 2026-27 = Sem 5

# All sems — most recent first so fetch loop finds data fast
SEM_IDS = [
    "CH20262701",   # Fall   2026-27 = Sem 5 (current)
    "CH20252605",   # Winter 2025-26 = Sem 4
    "CH20252601",   # Fall   2025-26 = Sem 3
    "CH20242505",   # Winter 2024-25 = Sem 2
    "CH20242501",   # Fall   2024-25 = Sem 1
    "CH20262705",   # Winter 2026-27 = Sem 6 (future)
    "CH20272801",   # Fall   2027-28 = Sem 7 (future)
    "CH20272805",   # Winter 2027-28 = Sem 8 (future)
]