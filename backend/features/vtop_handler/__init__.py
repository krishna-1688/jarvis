"""
Packages of vtop_handler.
---------------------------
Go to the individual package's documentation for more details.

- session_generator.get_valid_session: 
    Login to vtop and get a valid session.

- student_profile.get_student_profile: 
    Get the profile details dictionary of the student.

- student_timetable.get_timetable:
    Get the timetable dictionary of the student.

"""

# Lazy exports (PEP 562). Importing this package used to import every
# handler up front, which pulls in pandas + BeautifulSoup (~45 MB) — even
# when a caller only wanted `vtop_handler.constants`. The backend keeps
# running all day but only scrapes VTOP a few times, so these now load on
# first use.
import importlib

_EXPORTS = {
    "get_valid_session": "session_generator", "generate_session": "session_generator",
    "get_student_profile": "student_profile", "get_timetable": "student_timetable",
    "get_attendance": "student_attendance", "get_single_subject_attendance": "student_attendance",
    "get_acadhistory": "student_academic_history", "get_faculty_details": "faculty_handler",
    "get_academic_calender": "academic_calender_handler", "get_exam_schedule": "student_exam_schedule",
}
_SUBMODULES = {"session_generator", "student_profile", "student_timetable", "student_academic_history",
               "faculty_handler", "academic_calender_handler", "student_exam_schedule", "student_attendance"}


def __getattr__(name):
    if name in _EXPORTS:
        return getattr(importlib.import_module(f".{_EXPORTS[name]}", __name__), name)
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
