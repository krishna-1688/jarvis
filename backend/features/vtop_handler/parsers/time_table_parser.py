
from typing import Dict, List
from bs4 import BeautifulSoup
import pandas as pd
import io


# VTOP's real column is "L T P J C" (a combined Lecture-Tutorial-
# Practical-Project-Credits string, e.g. "3 0 0 0 3.0" — the LAST
# token is the credit value, not a standalone number). Other
# candidates are kept in case VTOP's naming varies. If none match,
# credits extraction returns {} (never raises) and prints the real
# column names so the right one can be added here.
_CREDIT_COLUMN_CANDIDATES = ["L T P J C", "Credits", "Credit", "L T P C", "LTPC", "L-T-P-C", "L-T-P-J-C"]

def _find_credits_column(df):
    for candidate in _CREDIT_COLUMN_CANDIDATES:
        for col in df.columns:
            if str(col).strip().lower() == candidate.lower():
                return col
    return None

def _extract_credits_value(raw):
    """VTOP's credits cell is a combined "L T P J C" string — the last
    whitespace-separated token is the actual credit value."""
    try:
        return float(raw)
    except (ValueError, TypeError):
        pass
    parts = str(raw).split()
    if not parts:
        return None
    try:
        return float(parts[-1])
    except (ValueError, TypeError):
        return None

def _get_course_credits_dic(df) -> Dict[str, float]:
    """
    Course code -> credits, additive to _get_course_code_dic (which is
    left untouched). Used by the CGPA predictor for this semester's
    course credits — see features/cgpa_predictor.py.

    `df` here is already the specific table (raw_df[0] from the
    caller) — not a list of tables.
    """
    credits_col = _find_credits_column(df)
    if credits_col is None:
        print(f"[time_table_parser] No credits column found. Available columns: {list(df.columns)}")
        return {}

    codes = df['Course'].str.split('-', n=1, expand=True)[0].str.strip()
    result = {}
    for code, raw in zip(codes, df[credits_col]):
        credits = _extract_credits_value(raw)
        if credits is not None:
            result[code] = credits
    return result


def _get_course_code_dic(df) -> Dict[str, str]:
    """ creating a dictionary of course code and course name ex:
        {
            "ECE4008": "Computer Communication",
            "ECE4015": "Data Structures",
        }

        `df` here is already the specific table (raw_df[0] from the
        caller) — not a list of tables.
    """
    df[["Course Code","Course Name"]] = df['Course'].str.split('-', n=1, expand=True)
    df["Course Code"] = df["Course Code"].str.strip()
    # VTOP appends a "( Theory Only )"/"( Embedded Lab )"-style suffix
    # to the same string — strip it so course names read naturally in
    # spoken responses ("Database Systems" not "Database Systems  ( Theory Only )").
    df["Course Name"] = (
        df["Course Name"].str.replace(r"\(.*?\)\s*$", "", regex=True)
        .str.strip()
    )
    return dict(df[["Course Code", "Course Name"]].to_dict("split")["data"])
 


def parse_timetable(timetable_html: str) -> Dict[str, List]:
    """takes the html of the timetable and returns the timetable in the form of a dictionary"""
    def _get_vals(s):  # temporary helper function
        """ gets the solt course code and class name for a row """
        temp_arr = str(s).strip().split("-")
        slot = temp_arr[0]
        course_code = temp_arr[1]
        cls = "-".join(temp_arr[3:])

        return slot, course_code, cls

    time_table = {"Monday": [], "Tuesday": [], "Wednesday": [],
                  "Thursday": [], "Friday": [], "Saturday": [], "Sunday": []}
    # helper dictionary to convert from short form to longer one
    _temp_dic = {"MON": "Monday", "TUE": 'Tuesday', "WED": 'Wednesday',
                 "THU": 'Thursday', "FRI": 'Friday', "SAT": "Saturday", "SUN": "Sunday"}

    # for the time table we have two tables one's with the course lectures and the other with the scheduled classes
    raw_df = pd.read_html(io.StringIO(timetable_html))
    df = raw_df[1]

    course_code_name_dic = _get_course_code_dic(raw_df[0])
    course_credits_dic   = _get_course_credits_dic(raw_df[0])

    # iterating over the rows of the table and converting to json format
    for row_idx in range(3, df.shape[0]):
        # The second col i.e idx 1 in data is the theory or lab
        is_theory = (df.iloc[row_idx, 1] == 'Theory')
        # The second col in data is the day of the week
        # The day is in short form i.e "TUE" we need to convert it to "Tuesday"
        day = _temp_dic.get(str(df.iloc[row_idx, 0]), "Sunday")

        for col_idx in range(2, df.shape[1]):
            cell = df.iloc[row_idx, col_idx]
            cell_str = str(cell).strip()
            is_cell_empty = cell_str.count(
                '-') < 3 or len(cell_str) <= 3 or all([char == '-' for char in cell_str])
            # if the cell is empty without data then we skip it
            if not is_cell_empty:
                slot, code, cls = _get_vals(df.iloc[row_idx, col_idx])
                time_table[day].append({
                    "slot": slot,
                    "courseName": course_code_name_dic[code],
                    "code": code,
                    "class": cls,
                    "credits": course_credits_dic.get(code),
                    "startTime": df.iloc[0, col_idx] if is_theory else df.iloc[2, col_idx],
                    "endTime": df.iloc[1, col_idx] if is_theory else df.iloc[3, col_idx],
                })

    return time_table
