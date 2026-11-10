"""Polite access to sec.gov: a declared User-Agent, throttling, and quarterly downloads."""

import gzip
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

USER_AGENT_ENV = "EDGAR_USER_AGENT"
DATASET_PAGE = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
# The SEC moved newer quarters to a new folder in 2026, so the link is looked up on the
# data-set page first, falling back to both folders seen so far.
KNOWN_FOLDERS = (
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/",
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/",
)
MIN_INTERVAL = 0.15  # seconds between requests: under 7 a second; the SEC allows 10

_QUARTER = re.compile(r"^(\d{4})q([1-4])$")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class SecError(Exception):
    """A problem reaching sec.gov, worded for the person running the tool."""


class NotFound(SecError):
    """sec.gov answered 404."""


def parse_quarter(text: str) -> str:
    """'2026Q3' -> '2026q3'. Rejects anything that isn't a quarter the data sets could have."""
    quarter = text.strip().lower()
    match = _QUARTER.match(quarter)
    if not match or int(match.group(1)) < 2006:
        raise ValueError(f"'{text}' is not a quarter like 2026q3 (the data sets start at 2006q1)")
    return quarter


def user_agent(explicit: str | None = None) -> str:
    """The SEC asks every automated client to identify itself with a name and contact email."""
    value = (explicit or os.environ.get(USER_AGENT_ENV, "")).strip()
    if not value:
        raise SecError(
            f'Set {USER_AGENT_ENV} to your name and email, e.g. "Jane Doe jane@example.com". '
            "The SEC requires a contact in the User-Agent of automated requests."
        )
    if not _EMAIL.search(value):
        raise SecError(f'{USER_AGENT_ENV} needs a contact email, e.g. "Jane Doe jane@example.com".')
    return value


class SecClient:
    """GETs from sec.gov with a declared User-Agent, spacing requests out."""

    def __init__(
        self,
        agent: str,
        min_interval: float = MIN_INTERVAL,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.agent = agent
        self.min_interval = min_interval
        self._open = opener
        self._last = float("-inf")

    def get(self, url: str) -> bytes:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(
            url, headers={"User-Agent": self.agent, "Accept-Encoding": "gzip"}
        )
        try:
            with self._open(request, timeout=120) as response:
                body = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as err:
            if err.code == 404:
                raise NotFound(f"{url} was not found on sec.gov") from err
            if err.code in (403, 429):
                raise SecError(
                    f"sec.gov refused the request (HTTP {err.code}). Check that {USER_AGENT_ENV} "
                    "holds a real name and email. After many requests, wait 10 minutes."
                ) from err
            raise SecError(f"sec.gov returned HTTP {err.code} for {url}") from err
        except urllib.error.URLError as err:
            raise SecError(f"Could not reach sec.gov: {err.reason}") from err
        finally:
            self._last = time.monotonic()


def find_zip_link(html: str, quarter: str) -> str | None:
    """Pull a quarter's zip link out of the SEC data-set page."""
    match = re.search(rf'href="([^"]*/{re.escape(quarter)}_form345\.zip)"', html, re.IGNORECASE)
    if not match:
        return None
    link = match.group(1)
    return link if link.startswith("http") else "https://www.sec.gov" + link


def candidate_urls(client: SecClient, quarter: str) -> list[str]:
    try:
        page = client.get(DATASET_PAGE).decode("utf-8", errors="replace")
    except NotFound:
        page = ""
    found = find_zip_link(page, quarter)
    urls = ([found] if found else []) + [
        f"{folder}{quarter}_form345.zip" for folder in KNOWN_FOLDERS
    ]
    return list(dict.fromkeys(urls))


def download_quarter(client: SecClient, quarter: str, dest_dir: Path) -> Path:
    """Download one quarter's zip into dest_dir. Later calls reuse the file."""
    dest = dest_dir / f"{quarter}_form345.zip"
    if dest.exists():
        return dest
    for url in candidate_urls(client, quarter):
        try:
            body = client.get(url)
        except NotFound:
            continue
        if not body.startswith(b"PK"):
            raise SecError(f"sec.gov sent something other than a zip file for {url}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(".part")
        partial.write_bytes(body)
        partial.replace(dest)
        return dest
    raise SecError(
        f"The SEC hasn't published {quarter} yet. Published quarters are listed at {DATASET_PAGE}"
    )
