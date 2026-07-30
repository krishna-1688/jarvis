"""
Returns Timetable in a dictionary form

    Usage:
    ------
    > import asyncio, aiohttp
    > from vtop_handler.session_generator import get_valid_session
    > form vtop_handler.student_timetable improt get_timetable
    >
    > async def main():
    >     async with aiohttp.ClientSession() as sess:
    >         user_name, valid = await get_valid_session(user_name, password, sess)
    >         time_table, valid = await get_timetable(sess, user_name)
    >         print(time_table)
    > 
    > if __name__ == "__main__":
    >     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    >     asyncio.run(main())

"""
from typing import Dict, Tuple, Union
import traceback

from .constants import VTOP_TIMETABLE_URL, HEADERS, SEM_IDS
from .payloads import get_timetable_payload 
from .parsers import parse_timetable

import asyncio
import aiohttp

async def _get_time_table_from_payload(sess:aiohttp.ClientSession, payload: Dict) -> Tuple[Dict, bool]:
    """
        Returns the timetable of the user in the form of a dictionary using the payload given
        which is mentioned above in the file containing this function.
    """

    valid = False
    time_table = {}
    
    async with sess.post(VTOP_TIMETABLE_URL, data=payload, headers=HEADERS) as resp:
        timetable_html = await resp.text()
        if resp.status == 200:
            try: 
                time_table = parse_timetable(timetable_html)
                valid = True
            except Exception as e:
                traceback.print_exc()
                
                print(f"payload: {payload}")
                print("Error in parsing the timetable with error: ", e)
        else:
            print("Error in getting timetable with payload: ", payload)

    return (time_table, valid)

async def get_timetable(
    sess: aiohttp.ClientSession, 
    username: str,
    csrf_token: str,
    semesterID: Union[str, None] = None) -> Tuple[
        Dict,  # timetable
        bool]: # valid i.e sucess of the session
    """
        Gets the timetable of the user for the given semesterID using the given session & username

        Parameters:
        -----------
        sess: aiohttp.ClientSession
            the session to use for the request
        username: str
            the username of the user
        semesterID: str
            the semesterID of the user

        Returns:
        --------
        time_table: dict[str, list]
            the time table of the user in the given semesterID
            time_table =  {
                "Monday": [
                |    
                |    {
                |    |    "slot" : "A1",
                |    |    "courseName" : "Computer Communication",
                |    |    "code" : "ECE4008",
                |    |    "class" : "AB1 408",
                |    |    startTime: "8:00",
                |    |    endTime:"8:50"
                |    },
                |
                |    {
                |    |    "slot" : "A2",
                |    |    "courseName" : "Computer Communication",
                |    |    "code" : "ECE4008",
                |    |    "class" : "AB1 408",
                |    |    startTime: "9:00",
                |    |    endTime:"9:50"
                |    }
                ]
            }

        valid: bool
            whether the request was successful or not
    """

    valid = False
    time_table = {}

    # When a specific semester is requested, only try that one — otherwise
    # fall through SEM_IDS in its declared order (current semester first;
    # see constants.py). Iterating a set() here previously discarded that
    # ordering, which is why timetable fetches were silently landing on
    # whichever semester VTOP happened to return first for a hash-ordered
    # set (a stale/older semester), while attendance (which already looped
    # over the plain list) was unaffected.
    sem_candidates = [semesterID] if semesterID else SEM_IDS
    for semID in sem_candidates:
        payload = get_timetable_payload(username, semID, csrf_token)
        time_table, valid = await _get_time_table_from_payload(sess, payload)
        if valid:
            break

    return (time_table, valid)
