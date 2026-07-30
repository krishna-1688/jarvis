import sys, os, asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import aiohttp
from config import VTOP_USERNAME, VTOP_PASSWORD
from features.vtop_handler.session_generator import get_valid_session
from features.vtop_handler.marks_view import get_marks_dict
from features.vtop_handler.constants import SEM_IDS
from core.memory import save_vtop_marks, get_all_marks_summary, get_subject_marks, get_specific_assessment, get_marks_synced_sems

CURRENT_SEM = "CH20262701"

async def test():
    async with aiohttp.ClientSession() as sess:
        print("Logging in...")
        username, csrf_token = await get_valid_session(VTOP_USERNAME, VTOP_PASSWORD, sess)
        print("Username:", username)
        print("Logged in:", bool(csrf_token))

        if not csrf_token:
            print("Login failed. Exiting.")
            return

        already_synced = get_marks_synced_sems()
        print(f"Already in DB: {already_synced}")

        for sem_id in SEM_IDS:
            # Skip completed sems already stored
            if sem_id != CURRENT_SEM and sem_id in already_synced:
                print(f"Skipping {sem_id} — already stored")
                continue

            print(f"\nFetching marks for {sem_id}...")
            try:
                marks = await get_marks_dict(sess, username, sem_id, csrf_token)
                if marks:
                    print(f"  Got {len(marks)} subjects")
                    save_vtop_marks(marks, sem_id)
                else:
                    print(f"  No data for {sem_id}")
            except Exception as e:
                print(f"  Error: {e}")

    print("\n--- All marks summary ---")
    print(get_all_marks_summary())

    print("\n--- Subject search: computation ---")
    print(get_subject_marks("computation"))

    print("\n--- Specific: computation CAT1 ---")
    print(get_specific_assessment("computation", "cat1"))

asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(test())