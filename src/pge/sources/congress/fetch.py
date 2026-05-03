"""Congress.gov v3 HTTP layer.

* Base: ``https://api.congress.gov/v3``
* Auth: ``api_key`` query param (same api.data.gov scheme as FEC; one key works for both).
* Rate limit: 5,000 req/hr per key.
* Pagination: ``limit`` (max 250) + ``offset``; response includes ``pagination.next``.
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

CONGRESS_BASE_URL = "https://api.congress.gov/v3"
DEFAULT_LIMIT = 250
DEFAULT_RAW_ROOT = Path("raw/congress")


class CongressError(RuntimeError):
    """Raised when the Congress.gov API returns a non-recoverable error."""


def get_api_key() -> str:
    """Read ``CONGRESS_API_KEY`` (or fall back to ``FEC_API_KEY``) from env."""
    key = os.environ.get("CONGRESS_API_KEY") or os.environ.get("FEC_API_KEY")
    if not key:
        raise CongressError(
            "CONGRESS_API_KEY env var not set. Get a free key at https://api.data.gov/signup/"
        )
    return key


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException)
    ),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _request(client: httpx.Client, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Single Congress.gov call with retry on transient failures."""
    resp = client.get(f"{CONGRESS_BASE_URL}/{endpoint.lstrip('/')}", params=params)
    if resp.status_code >= 400:
        # 4xx other than 429 is non-retryable.
        if resp.status_code not in {429, 500, 502, 503, 504}:
            raise CongressError(
                f"Congress {endpoint} -> {resp.status_code}: {resp.text[:200]}"
            )
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


def paginate(
    endpoint: str,
    params: dict[str, Any],
    *,
    api_key: str,
    results_key: str,
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
    client: httpx.Client | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield every row of ``endpoint``. Uses limit/offset pagination."""
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        page_params = {"limit": DEFAULT_LIMIT, "offset": 0, "format": "json", **params,
                       "api_key": api_key}
        page_index = 0
        while True:
            payload = _request(client, endpoint, page_params)
            if archive:
                _archive(raw_root, endpoint, page_index, payload)
            rows = payload.get(results_key, []) or []
            yield from rows

            page_index += 1
            if max_pages is not None and page_index >= max_pages:
                return
            if not rows:
                return

            pagination = payload.get("pagination") or {}
            if not pagination.get("next"):
                return
            page_params["offset"] = page_params["offset"] + page_params["limit"]
    finally:
        if own_client:
            client.close()


def get(
    endpoint: str,
    *,
    api_key: str,
    params: dict[str, Any] | None = None,
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Single GET (used for detail endpoints like ``/member/{bioguideId}``)."""
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        merged = {"format": "json", **(params or {}), "api_key": api_key}
        payload = _request(client, endpoint, merged)
        if archive:
            _archive(raw_root, endpoint, 0, payload)
        return payload
    finally:
        if own_client:
            client.close()


def iter_members(
    *,
    api_key: str,
    current_only: bool = True,
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate the ``/member`` summary list."""
    params: dict[str, Any] = {}
    if current_only:
        params["currentMember"] = "true"
    yield from paginate(
        "member", params, api_key=api_key, results_key="members",
        raw_root=raw_root, archive=archive, max_pages=max_pages,
    )


def get_member_detail(bioguide_id: str, *, api_key: str, **kw: Any) -> dict[str, Any]:
    """Fetch the full ``/member/{bioguideId}`` detail document."""
    return get(f"member/{bioguide_id}", api_key=api_key, **kw)


def iter_committees(
    *,
    api_key: str,
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate the ``/committee`` summary list (both chambers + joint)."""
    yield from paginate(
        "committee", {}, api_key=api_key, results_key="committees",
        raw_root=raw_root, archive=archive, max_pages=max_pages,
    )


def get_committee_detail(
    chamber: str, committee_code: str, *, api_key: str, **kw: Any
) -> dict[str, Any]:
    """Fetch ``/committee/{chamber}/{systemCode}`` -- includes member roster."""
    return get(f"committee/{chamber}/{committee_code}", api_key=api_key, **kw)
