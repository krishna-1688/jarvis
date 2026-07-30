"""
lms_handler/assignment_status.py
Checks submission status of an individual assignment.
"""

from bs4 import BeautifulSoup


def get_submission_status(html: str) -> str:
    """
    Parses the assignment view page HTML.
    Returns: 'submitted' | 'not_submitted' | 'unknown'
    """
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("div", class_="submissionstatustable")
    if not table:
        return "unknown"

    for row in table.find_all("tr"):
        th = row.find("th")
        td = row.find("td")
        if not th or not td:
            continue
        if "submission status" in th.get_text(strip=True).lower():
            status_text = td.get_text(strip=True).lower()
            if any(w in status_text for w in [
                "submitted for grading",
                "submitted",
                "graded"
            ]):
                return "submitted"
            else:
                return "not_submitted"

    return "unknown"