"""
    Gets attendance from VTOP — fixed version.
    BUG FIX: Removed hardcoded file write to ./htmls/attd.html which crashes
             if the directory doesn't exist.
"""

from .constants import HEADERS, VTOP_ATTENDANCE_URL, SEM_IDS, VTOP_SINGLE_SUBJECT_ATTENDANCE_URL
from .payloads import generate_payload_attendance_for_subject, get_attendance_payload
from .parsers import parse_attendance, parse_single_subject_attendance

import asyncio
import aiohttp
from typing import Dict, List, Tuple, Union


async def _get_attendance_from_payload(
    sess: aiohttp.ClientSession,
    payload: Dict,
    username: str,
    semID: str,
    crsf_token: str
) -> Tuple[dict, bool]:

    valid = False
    attendance = {}

    async with sess.post(VTOP_ATTENDANCE_URL, data=payload, headers=HEADERS) as resp:
        attendance_html = await resp.text()

        if resp.status != 200:
            print(f"Attendance: bad status {resp.status} for sem {semID}")
            return ({}, False)

        # BUG FIX: guard against error HTML before parsing
        if "<table" not in attendance_html.lower() or "HTTP Status" in attendance_html:
            return ({}, False)

        try:
            # BUG FIX: removed `with open("./htmls/attd.html", 'w')` — crashes if dir missing
            attendance = parse_attendance(attendance_html)

            # Fetch per-subject attendance detail in parallel
            tasks = [
                asyncio.create_task(
                    get_single_subject_attendance(
                        sess,
                        username,
                        subj.get("courseId", None),
                        subj.get("courseShortType", None),
                        semID,
                        crsf_token
                    )
                )
                for subj in attendance.values()
            ]
            await asyncio.gather(*tasks)

            for slot, task in zip(attendance.keys(), tasks):
                attendance[slot]["history"] = await task

            valid = True

        except Exception as e:
            print(f"Attendance parse error for sem {semID}: {e}")
            return ({}, False)

    valid = False if attendance == {} else valid
    return (attendance, valid)


async def get_attendance(
    sess: aiohttp.ClientSession,
    username: str,
    crsf_token: str,
    semesterID=None
) -> Tuple[dict, bool]:

    valid = False
    attendance = {}

    # Same fix as get_timetable()/get_exam_schedule() (H.4): honor an
    # explicit semesterID (needed to fetch a SPECIFIC past semester's
    # attendance, not just whichever one happens to answer first) rather
    # than always looping every SEM_IDS entry looking for the first hit.
    sem_candidates = [semesterID] if semesterID else SEM_IDS
    for semID in sem_candidates:
        payload = get_attendance_payload(username, semID, crsf_token)
        attendance, valid = await _get_attendance_from_payload(sess, payload, username, semID, crsf_token)
        if valid:
            print(f"Attendance found for sem: {semID}")
            break

    return (attendance, valid)


async def get_single_subject_attendance(
    sess: aiohttp.ClientSession,
    username: str,
    course_id: Union[str, None],
    course_type: Union[str, None],
    sem_id: str,
    crsf_token: str
) -> List[Dict[str, str]]:

    if course_id is None:
        return []
    if course_type is None:
        return []

    attd_payload = generate_payload_attendance_for_subject(
        sem_id, course_id, course_type, username, crsf_token
    )
    try:
        async with sess.post(
            VTOP_SINGLE_SUBJECT_ATTENDANCE_URL,
            data=attd_payload,
            headers=HEADERS
        ) as resp:
            txt = await resp.text()
            if "<table" not in txt.lower():
                return []
            return parse_single_subject_attendance(txt)
    except Exception as e:
        print(f"Single subject attendance error: {e}")
        return []