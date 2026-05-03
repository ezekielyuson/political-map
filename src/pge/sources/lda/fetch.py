"""LDA API HTTP layer (``lda.senate.gov/api/v1``).

* No api.data.gov involvement -- this is the Senate's own API.
* Auth is *optional* via ``LDA_API_KEY`` (header ``Authorization: Token <key>``).
  Anonymous works but is rate-limited to ~75 req/min; keyed gets ~120/min.
* Pagination: response includes a ``next`` URL; we follow it. Default
  ``page_size`` is 25 and capped to 25 at the time of writing.

Why API instead of bulk XML?
----------------------------
The spec mentions "quarterly XML downloads," but the official LDA API exposes
the same data as JSON, paginated, with first-class incremental filters
(``dt_posted_after``). It also matches the FEC / Congress patterns we already
have, so the ingestor stays uniform. Bulk XML is still the right tool for a
full historical backfill -- documented in ``README.md`` as the alternative.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

LDA_BASE_URL = "https://lda.senate.gov/api/v1"
DEFAULT_PAGE_SIZE = 25
DEFAULT_RAW_ROOT = Path("raw/lda")


class LDAError(RuntimeError):
    """Non-recoverable LDA API error."""


def get_api_key() -> str | None:
    """``LDA_API_KEY`` is optional; returns None for anonymous mode."""
    return os.environ.get("LDA_API_KEY") or None


def _auth_headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Token {api_key}"} if api_key else {}


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException)
    ),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _request(
    client: httpx.Client, url: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One LDA call. Tenacity-retries 429/5xx + transport errors."""
    resp = client.get(url, params=params)
    if resp.status_code >= 400:
        if resp.status_code not in {429, 500, 502, 503, 504}:
            raise LDAError(f"LDA {url} -> {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
    return resp.json()


def _archive(raw_root: Path, endpoint: str, page_index: int, payload: dict[str, Any]) -> Path:
    bucket = raw_root / endpoint.replace("/", "_").strip("_")
    bucket.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, sort_keys=True).encode()
    digest = hashlib.sha1(body).hexdigest()[:12]
    path = bucket / f"page-{page_index:05d}-{digest}.json"
    path.write_bytes(body)
    return path


# Map our short period codes to LDA's period strings.
PERIOD_MAP = {
    "q1": "first_quarter",
    "q2": "second_quarter",
    "q3": "third_quarter",
    "q4": "fourth_quarter",
    "h1": "mid_year",
    "h2": "year_end",
}

# Inverse for display / quarter formatting.
PERIOD_TO_QUARTER = {
    "first_quarter": "Q1",
    "second_quarter": "Q2",
    "third_quarter": "Q3",
    "fourth_quarter": "Q4",
    "mid_year": "H1",
    "year_end": "H2",
}


def normalize_period(period: str) -> str:
    """Accept 'q1' / 'first_quarter' / 'Q1' interchangeably."""
    period = period.strip().lower()
    if period in PERIOD_MAP:
        return PERIOD_MAP[period]
    return period


def iter_filings(
    *,
    api_key: str | None = None,
    filing_year: int | None = None,
    filing_period: str | None = None,
    dt_posted_after: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
    client: httpx.Client | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate ``/filings/`` rows, following the ``next`` URL until exhausted."""
    own_client = client is None
    client = client or httpx.Client(
        timeout=60.0, follow_redirects=True, headers=_auth_headers(api_key)
    )
    try:
        params: dict[str, Any] = {"page_size": page_size, "ordering": "dt_posted"}
        if filing_year is not None:
            params["filing_year"] = filing_year
        if filing_period:
            params["filing_period"] = normalize_period(filing_period)
        if dt_posted_after:
            params["dt_posted_after"] = dt_posted_after

        url: str | None = f"{LDA_BASE_URL}/filings/"
        page_index = 0
        first = True
        while url:
            payload = _request(client, url, params if first else None)
            first = False
            if archive:
                _archive(raw_root, "filings", page_index, payload)
            yield from payload.get("results", []) or []
            url = payload.get("next")
            page_index += 1
            if max_pages is not None and page_index >= max_pages:
                return
    finally:
        if own_client:
            client.close()
