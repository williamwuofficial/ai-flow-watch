"""Shared HTTP session with retries and a polite delay between calls to the same host."""
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# Some hosts (e.g. FRED) hang on browser UAs but answer plain clients.
PLAIN_UA = "ai-flow-watch/0.1 (+https://github.com/williamwuofficial/ai-flow-watch)"

_session = requests.Session()
_session.mount(
    "https://",
    HTTPAdapter(max_retries=Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])),
)
_last_call: dict[str, float] = {}


def get(url: str, *, params=None, ua: str = BROWSER_UA, delay: float = 0.5, timeout: int = 60) -> requests.Response:
    host = urlparse(url).netloc
    wait = _last_call.get(host, 0) + delay - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        r = _session.get(
            url,
            params=params,
            timeout=timeout,
            headers={"User-Agent": ua, "Accept": "application/json, text/csv, text/html, */*"},
        )
    finally:
        _last_call[host] = time.monotonic()
    r.raise_for_status()
    return r
