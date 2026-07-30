import pandas as pd
import datetime
import io
from typing import Dict
from bs4 import BeautifulSoup
import re

CourseCode = str | None # AM_CSE1005_00100
CourseType = str | None # ETH, ELA
# pattern to extract text between ()
pattern = re.compile(r'\((.*?)\)')

def parse_attendance(attendance_html: str) -> Dict[str, Dict] :
    """
        Parses the attendance html and returns a dictionary of attendance details.
        :check student attendance.py for more details on the structure of the dictionary.

        VTOP's attendance table now returns already-split columns
        (Course Code, Course Title, Course Type, Slot, Faculty Name,
        Attended Classes, Total Classes, Attendance Percentage)
        instead of the combined "Course Detail"/"Class Detail" strings
        this used to split on "-" — reads them directly. The last row
        is a "Total Number Of Credits: N" footer, not course data.
    """

    raw_df = pd.read_html(io.StringIO(attendance_html))
    df = raw_df[0]
    course_id, course_type_short = _extract_course_code_type_list(attendance_html)
    df["Course Id"] = course_id
    df["Course Type Short"] = course_type_short
    # removing redundant spaces in cols
    df.columns = [' '.join(str(col).strip().split()) for col in df.columns]

    attendace_dict = dict()
    # The last row is a "Total Number Of Credits" footer, not course data
    for row in range(df.shape[0]-1):
        slot = df.iloc[row]['Slot']

        attendace_dict[slot] = {
            "attended" : df.iloc[row]['Attended Classes'],
            "total" : df.iloc[row]['Total Classes'],
            "percentage" : df.iloc[row]['Attendance Percentage'],
            "faculty" : df.iloc[row]['Faculty Name'],
            "courseName" : df.iloc[row]['Course Title'],
            "code" : df.iloc[row]['Course Code'],
            "courseId" : df.iloc[row]['Course Id'],
            "courseShortType" : df.iloc[row]['Course Type Short'],
            "type" : df.iloc[row]['Course Type'],
            "subjectId" : None,
            "updatedOn" : datetime.datetime.now().strftime("%c")
        }
    return attendace_dict

def _extract_course_code_and_type(tr) -> tuple[CourseCode, CourseType]:
    # getting all the td's 
    tds = tr.find_all("td")
    # getting the class id from last td 
    last_td = tds[-1]
    # extracting onlick from the <td> <a> </a> </td>
    t = last_td.a["onclick"]
    # extracting ('AP2023246','21BCE9853','AM_CSE1005_00100','ETH')
    params = pattern.search(t)
    if params is None: 
        return (None, None)
    # getting the params as list 
    params_list = ( params.group()
                   .replace(")", "")
                   .replace("(", "")
                   .replace("'", "")
                   .split(","))
    # getting course_code, course_type 
    course_code = params_list[2]
    course_type = params_list[3]

    return course_code, course_type 

def _extract_course_code_type_list(html: str) -> tuple[list[CourseCode], list[CourseType]]:
    soup = BeautifulSoup(html)
    # getting all the tr's from page
    trs = soup.find_all("tr")
    # helper arrs for saving res
    course_code_list: list[CourseCode] = []
    course_type_list: list[CourseType] = []
    # finding all the tr's and not iterating header
    for tr in trs[1:]:
        try: 
            course_code, course_type = _extract_course_code_and_type(tr)
            course_code_list.append(course_code)
            course_type_list.append(course_type)
        except (IndexError, TypeError) as e:
            course_type_list.append(None)
            course_code_list.append(None)
    return (course_code_list, course_type_list)
