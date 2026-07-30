"""
lms_handler/calendar_scraper.py
Parses VIT Chennai Moodle upcoming calendar page.
"""

from bs4 import BeautifulSoup
from datetime import datetime
import re


def parse_calendar(html: str) -> list:
    """
    Parses the upcoming calendar page HTML.
    Returns list of dicts:
    {
        title, course_name, course_url,
        due_date (datetime), due_date_str,
        assign_url, description
    }
    """
    soup       = BeautifulSoup(html, "html.parser")
    assignments = []

    for card in soup.find_all("div", class_="card"):
        # Must have assignment icon (monologo from assign module)
        img = card.find("img", alt="Activity event")
        if not img or "assign" not in img.get("src", ""):
            continue

        # Title
        title_el = card.find("h3", class_="name")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        # Strip " is due" suffix Moodle adds
        title = re.sub(r"\s+is due\s*$", "", title, flags=re.IGNORECASE).strip()

        # Due date — inside col-11 next to clock icon
        due_date_str = ""
        due_date     = None
        time_link    = card.find("i", title="When")
        if time_link:
            time_row = time_link.find_parent("div", class_="row")
            if time_row:
                col = time_row.find("div", class_="col-11")
                if col:
                    due_date_str = col.get_text(strip=True)
                    # Parse: "Wednesday, 22 July, 11:59 PM"
                    due_date = _parse_due_date(due_date_str)

        # Course name + URL
        course_name = ""
        course_url  = ""
        course_icon = card.find("i", title="Course")
        if course_icon:
            course_row = course_icon.find_parent("div", class_="row")
            if course_row:
                col = course_row.find("div", class_="col-11")
                if col:
                    a = col.find("a")
                    if a:
                        course_name = a.get_text(strip=True)
                        course_url  = a.get("href", "")

        # Description
        description = ""
        desc_el = card.find("div", class_="description-content")
        if desc_el:
            description = desc_el.get_text(strip=True)

        # Assignment submission URL (from "Add submission" or footer link)
        assign_url = ""
        footer = card.find("div", class_="card-footer")
        if footer:
            a = footer.find("a")
            if a:
                href = a.get("href", "")
                # Normalize to view URL (strip action param)
                assign_url = re.sub(r"&action=\w+", "", href)

        if title and due_date:
            assignments.append({
                "title":        title,
                "course_name":  course_name,
                "course_url":   course_url,
                "due_date":     due_date,
                "due_date_str": due_date_str,
                "assign_url":   assign_url,
                "description":  description,
            })

    return assignments


def _parse_due_date(text: str) -> datetime | None:
    """
    Parse Moodle due date strings like:
    'Wednesday, 22 July, 11:59 PM'
    'Monday, 28 July, 11:59 PM'
    """
    try:
        # Strip weekday name
        cleaned = re.sub(r"^[A-Za-z]+,\s*", "", text).strip()
        # "22 July, 11:59 PM"  or  "22 July 11:59 PM"
        cleaned = cleaned.replace(",", "")
        year = datetime.now().year
        dt   = datetime.strptime(f"{cleaned} {year}", "%d %B %I:%M %p %Y")
        # If the parsed date is more than 6 months in the past, assume next year
        if (datetime.now() - dt).days > 180:
            dt = dt.replace(year=year + 1)
        return dt
    except Exception:
        return None