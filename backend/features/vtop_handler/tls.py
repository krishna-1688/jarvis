"""
TLS context for vtopcc.vit.ac.in.

VTOP's server sends only its leaf certificate, not the Sectigo
intermediate that links it to a trusted root. Browsers quietly download
the missing intermediate; Python doesn't, so every login failed with
CERTIFICATE_VERIFY_FAILED ("unable to get local issuer certificate") —
except when Windows happened to have that intermediate cached from a
browser visit, which is why VTOP sync worked on some days and not others.

Verification stays fully on: the public intermediate is bundled (fetched
from the URL in VTOP's own certificate, valid until 2036, chains to
Sectigo Public Server Authentication Root R46 in certifi).
"""

import os
import ssl
from functools import lru_cache

import certifi

_INTERMEDIATE = os.path.join(os.path.dirname(__file__), "certs",
                             "sectigo_public_server_auth_ca_dv_r36.pem")


@lru_cache(maxsize=1)
def vtop_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        ctx.load_default_certs()
    except Exception:
        pass
    if os.path.exists(_INTERMEDIATE):
        ctx.load_verify_locations(cafile=_INTERMEDIATE)
    return ctx


def vtop_client_session(**kwargs):
    """aiohttp.ClientSession that can verify VTOP's certificate."""
    import aiohttp
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=vtop_ssl_context()), **kwargs)
