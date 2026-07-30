import sys, os, asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import aiohttp
from features.vtop_handler.constants import VTOP_BASE_URL, VTOP_PRE_LOGIN, VTOP_LOGIN_PAGE_REDIRECT, VTOP_LOGIN_URL, HEADERS
from features.vtop_handler.session_generator import get_csrf_from_input

async def test():
    async with aiohttp.ClientSession() as sess:
        html = await sess.get(VTOP_BASE_URL, headers=HEADERS)
        text = await html.text()
        csrf = get_csrf_from_input(text)
        print("CSRF:", csrf)

        await sess.post(VTOP_PRE_LOGIN, data={"_csrf": csrf, "flag": "VTOP"}, headers=HEADERS)
        await sess.get(VTOP_LOGIN_PAGE_REDIRECT, headers=HEADERS)

        async with sess.get(VTOP_LOGIN_URL, headers=HEADERS) as resp:
            login_html = await resp.text()
            print("Login page length:", len(login_html))
            # Save to file so we can inspect it
            with open("login_page.html", "w", encoding="utf-8") as f:
                f.write(login_html)
            print("Saved to login_page.html")

asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(test())