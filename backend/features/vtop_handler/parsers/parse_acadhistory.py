import pandas as pd
import io
from bs4 import BeautifulSoup


from typing import Dict, List


def parse_acadhistory(acad_html: str)-> Dict[str, List]:

    # if the student has no academic history
    soup = BeautifulSoup(acad_html, "lxml")
    txt = soup.select('form > div > h3')
    if len(txt) > 0 and txt[0].text.replace(" ", '') == "NoRecordsFound":
        return { "summary": [], "subjects": [] }

    # actual parsing starts here
    raw_df = pd.read_html(io.StringIO(acad_html))

    grades_df = raw_df[1].copy()
    summary_df = raw_df[-1].copy()
    # convering summary to dict
    cols = summary_df.columns
    dic = {"summary":{k.replace(" ", "").replace("Grades", "") : float(summary_df.iloc[0][k]) for k in cols[:-1]}} # type: ignore
    # VTOP's grade history table doesn't use a real <thead> — the
    # actual column labels (Sl.No., Course Code, Course Title, Course
    # Type, Credits, Grade, ...) are just the FIRST data row, not row 1.
    header_row = grades_df.iloc[0]
    grades_df.drop([0], axis=0, inplace=True)
    grades_df.columns = [" ".join(str(ele).split()) for ele in header_row]

    # Full per-course rows (code/title/credits/grade) — richer than
    # "subjects" alone, and the only source in this project for
    # per-course credits across every semester (not just the current
    # one). Used by features/cgpa_predictor.py.
    courses = []
    for _, row in grades_df.iterrows():
        courses.append({
            "code": row.get("Course Code"),
            "title": row.get("Course Title"),
            "credits": row.get("Credits"),
            "grade": row.get("Grade"),
        })

    # taking only the nessasary cols
    subjects_df = grades_df[["Course Title", "Grade"]].copy()
    # setting the index to the course title
    subjects_df.set_index("Course Title", inplace=True)
    # changed dictionary will have the key as subjects and value as grade
    subjects_df.columns = ["subjects"]
    # | is similar to + in lists
    return dic | subjects_df.to_dict() | {"courses": courses} # type: ignore
