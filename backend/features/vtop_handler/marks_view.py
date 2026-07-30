from typing import Any, Dict, Hashable, List, Union
import aiohttp

from .parsers import parse_marks_page
from .payloads import get_marks_view_payload
from .constants import HEADERS, MARKS_VIEW_PAGE

marksObjectType = Dict[str, Union[str, None]]
marksItemType = Union[List[marksObjectType], str, None, int, float]


async def get_marks_dict(
    sess: aiohttp.ClientSession,
    roll_no: str,
    sem_id: str,
    csrf_token: str = ""
) -> List[Dict[Hashable, marksItemType]]:

    payload = get_marks_view_payload(sem_id, roll_no, csrf_token)

    async with sess.post(MARKS_VIEW_PAGE, data=payload, headers=HEADERS) as resp:
        html = await resp.text()

        # Guard 1: bad HTTP status
        if resp.status != 200:
            print(f"  [marks] HTTP {resp.status} for sem {sem_id}")
            return []

        # Guard 2: VTOP returned an error page
        if "HTTP Status 404" in html or "HTTP Status 403" in html:
            print(f"  [marks] Error page for sem {sem_id}")
            return []

        # Guard 3: no table means no data for this sem
        if "<table" not in html.lower():
            print(f"  [marks] No table in response for sem {sem_id}")
            return []

        # Guard 4: VTOP sometimes returns a "No Records Found" page
        if "No Record" in html or "no record" in html.lower():
            print(f"  [marks] No records for sem {sem_id}")
            return []

        try:
            result = parse_marks_page(html)
            return result if result else []
        except Exception as e:
            print(f"  [marks] Parse error for sem {sem_id}: {e}")
            return []