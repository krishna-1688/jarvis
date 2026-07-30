"""
    Solves the captcha and logins into the website 
    this session can be used to extract further data
"""

from bs4 import BeautifulSoup
from typing import Tuple 
import aiohttp
from typing import Union

from .Exceptions.captcha_failure import CaptchaFailure
from .Exceptions.invalid_credentials import InvalidCredentialsException
from .Exceptions.invalid_crsf_token import InvalidCRSFToken 

from .utils import find_image
from .constants import VTOP_LOGIN_URL, VTOP_BASE_URL, HEADERS, VTOP_PRE_LOGIN, VTOP_LOGIN_PAGE_REDIRECT
from .captcha_solver import solve_captcha

def get_csrf_from_input(html: str) -> str | None:
    soup = BeautifulSoup(html, 'html.parser')
    csrf_input = soup.find('input', attrs={'name': '_csrf'})
    if csrf_input is None: 
        return None
    return csrf_input.get("value")

async def init(sess: aiohttp.ClientSession) -> str | None: 
    _html = await sess.get(VTOP_BASE_URL, headers=HEADERS)
    return get_csrf_from_input(await _html.text())

async def setup(sess: aiohttp.ClientSession, csrf_token: str):
    payload = {"_csrf": csrf_token, "flag": "VTOP"}
    await sess.post(VTOP_PRE_LOGIN, data=payload, headers=HEADERS)

async def page(sess: aiohttp.ClientSession):
    await sess.get(VTOP_LOGIN_PAGE_REDIRECT, headers=HEADERS)

async def get_captcha(sess: aiohttp.ClientSession) -> tuple[str, str | None]:
    token = await init(sess)
    if token is None:
        raise InvalidCRSFToken(status_code=500)
    await setup(sess, token)
    await page(sess)
    
    # Get login page to get fresh csrf
    async with sess.get(VTOP_LOGIN_URL, headers=HEADERS) as resp:
        login_html = await resp.text()
        token = get_csrf_from_input(login_html) or token

    # Fetch captcha from dedicated endpoint
    async with sess.get("https://vtopcc.vit.ac.in/vtop/get/new/captcha", headers=HEADERS) as resp:
        captcha_html = await resp.text()
        captcha = find_image(captcha_html)

    return token, captcha
async def generate_session(
    username: str,
    password: str,
    sess: aiohttp.ClientSession
) -> Tuple[Union[str, None], str]:
    """
    Logs into VTOP, returns (roll_no, csrf_token) on success.
    Raises CaptchaFailure or InvalidCredentialsException on failure.
    """
    crsf_token, captcha_src = await get_captcha(sess)

    if captcha_src is None:
        solved_captcha = ""
    else:
        solved_captcha = solve_captcha(captcha_src)

    payload = {
        "_csrf": crsf_token,
        "username": username,
        "password": password,
        "captchaStr": solved_captcha
    }

    async with sess.post(VTOP_LOGIN_URL, data=payload, headers=HEADERS) as resp:
        post_login_html = await resp.text()
        soup = BeautifulSoup(post_login_html, 'lxml')

        error_type = soup.find('span', {"class": "text-danger", "role": "alert"})
        if error_type is not None:
            error_text = error_type.text.replace("\n", "").strip()
            if error_text == "Invalid LoginId/Password":
                raise InvalidCredentialsException(401)
            if error_text == "Invalid Captcha":
                raise CaptchaFailure("Captcha can't be solved")

        roll_no_ele = soup.find('span', {"class": "navbar-text text-light small fw-bold"})
        if roll_no_ele is None:
            raise InvalidCredentialsException(401)

        roll_no_text = roll_no_ele.text
        crsf_token = get_csrf_from_input(post_login_html)

        if crsf_token is None:
            raise InvalidCRSFToken(status_code=500)

        return roll_no_text.split()[0], crsf_token


async def get_valid_session(
    username: str,
    password: str,
    sess: aiohttp.ClientSession
) -> Tuple[Union[str, None], str]:
    """
    Tries up to 5 times to login, returns (roll_no, csrf_token).
    Returns (username, "") if all attempts fail.
    """
    csrf_token = ""
    roll_no = username

    for _ in range(5):
        try:
            roll_no, csrf_token = await generate_session(username, password, sess)
            if csrf_token:
                print(f"{roll_no} logged in successfully.")
                return roll_no, csrf_token
        except CaptchaFailure:
            print("Captcha failed, retrying...")
            continue
        except Exception as e:
            print(f"Login error: {e}")
            break

    print(f"Login failed for {username}")
    return roll_no, csrf_token