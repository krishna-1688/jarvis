"""
Marks page parser — rewritten for VTOP CBCS layout (VIT Chennai).

Actual HTML structure:
  <outer table>
    <tr> header: Sl.No. | ClassNbr | Course Code | Course Title | ... | Course Mode </tr>
    <tr> subject 1:  9 tds with subject data </tr>
    <tr> marks 1:    1 td (colspan=9) containing a nested <table> of marks </tr>
    <tr> subject 2:  9 tds with subject data </tr>
    <tr> marks 2:    1 td (colspan=9) containing a nested <table> of marks </tr>
    ...
  </outer table>

Pattern: alternating subject_row / marks_row pairs after the header.
A subject row has 9 direct tds.
A marks row has 1 direct td which contains an inner <table>.
"""

from bs4 import BeautifulSoup
from typing import List, Dict, Any


def _parse_marks_table(table_tag) -> List[Dict[str, Any]]:
    """Parse a nested marks <table> tag into a list of mark dicts."""
    rows = table_tag.find_all('tr')
    if not rows:
        return []

    # Row 0 is the header
    headers = [th.get_text(strip=True) for th in rows[0].find_all(['th', 'td'])]

    marks = []
    for row in rows[1:]:
        cells = row.find_all(['td', 'th'])
        if not cells:
            continue

        values = [c.get_text(strip=True) for c in cells]

        # Pad or trim to match header length
        while len(values) < len(headers):
            values.append(None)
        values = values[:len(headers)]

        entry = dict(zip(headers, values))
        entry.pop('Sl.No.', None)

        # Empty string → None
        entry = {k: (v if v and str(v).strip() else None) for k, v in entry.items()}
        marks.append(entry)

    return marks


def parse_marks_page(html: str) -> List[Dict[str, Any]]:
    """
    Parse VTOP marks page HTML.

    Returns list of subject dicts:
    [
        {
            "ClassNbr":     "CH2025260500100",
            "Course Code":  "BSTS102P",
            "Course Title": "Quantitative Skills Practice II",
            "Course Type":  "Soft Skill",
            "Course System":"CBCS",
            "Faculty":      "SIXPHRASE(APT)",
            "Slot":         "D1+TD1",
            "Course Mode":  "SBC01",
            "marks": [
                {
                    "Mark Title":           "Assessment - 1",
                    "Max. Mark":            "15",
                    "Weightage %":          "15",
                    "Status":               "Present",
                    "Scored Mark":          "15.0",
                    "Weightage Mark":       "15",
                    "Class Average":        None,
                    "Mark Posted Strength": None,
                    "Remark":               None
                },
                ...
            ]
        },
        ...
    ]
    """
    soup = BeautifulSoup(html, 'lxml')

    # Find the outer marks table — look for the one with ClassNbr in its header
    outer_table = None
    for table in soup.find_all('table'):
        first_row = table.find('tr')
        if first_row and ('ClassNbr' in first_row.get_text() or 'Course Code' in first_row.get_text()):
            outer_table = table
            break

    if outer_table is None:
        return []

    # Walk direct child rows only
    tbody = outer_table.find('tbody')
    parent = tbody if tbody else outer_table
    all_rows = parent.find_all('tr', recursive=False)

    if len(all_rows) < 2:
        return []

    # Row 0 is the header — skip it
    results = []
    i = 1  # start after header

    while i < len(all_rows):
        row = all_rows[i]
        tds = row.find_all('td', recursive=False)

        # Detect subject row: has 9 tds and no inner table
        is_subject_row = (len(tds) >= 8 and row.find('table') is None)

        if is_subject_row:
            cell_values = [td.get_text(strip=True) for td in tds]

            subject = {
                "ClassNbr":     cell_values[1] if len(cell_values) > 1 else None,
                "Course Code":  cell_values[2] if len(cell_values) > 2 else None,
                "Course Title": cell_values[3] if len(cell_values) > 3 else None,
                "Course Type":  cell_values[4] if len(cell_values) > 4 else None,
                "Course System":cell_values[5] if len(cell_values) > 5 else None,
                "Faculty":      cell_values[6] if len(cell_values) > 6 else None,
                "Slot":         cell_values[7] if len(cell_values) > 7 else None,
                "Course Mode":  cell_values[8] if len(cell_values) > 8 else None,
                "marks":        []
            }

            # Check if next row is the marks row for this subject
            if i + 1 < len(all_rows):
                next_row = all_rows[i + 1]
                next_tds = next_row.find_all('td', recursive=False)
                inner_table = next_row.find('table')

                # Marks row: 1 td with an inner table
                if len(next_tds) == 1 and inner_table:
                    subject['marks'] = _parse_marks_table(inner_table)
                    i += 2  # skip both subject row and marks row
                else:
                    i += 1  # only skip subject row
            else:
                i += 1

            if subject.get('Course Code') and subject['Course Code'].strip():
                results.append(subject)
        else:
            i += 1

    return results