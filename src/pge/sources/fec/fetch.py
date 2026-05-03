"""FEC HTTP layer.

Pagination
----------
The FEC API exposes two pagination modes:

* **Offset** (``page`` + ``per_page``) -- works for the first 10,000 results.
  Used for ``/committees/`` and ``/candidates/``.
* **Cursor** (``last_index`` + ``last_<sort>``) -- required for deeper queries,
  notably ``/schedules/schedule_a/`` (contributions). The cursor values for the
  next page come back in ``pagination.last_indexes``.

We handle both: :func:`paginate` first tries offset, falls back to cursor when
the response indicates more pages than offset can reach.

Rate limits
-----------
1,000 requests/hour per API key. We rely on tenacity to back off on 429s and
5xx, but if you blow the budget the only fix is to wait or get a second key.

Raw archival
------------
Every page response is written to ``raw/fec/<endpoint>/<page-id>.json`` so we
can re-parse without re-fetching. ``page-id`` is the request's content hash so
re-runs are idempotent.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

FEC_BASE_URL = "https://api.open.fec.gov/v1"
DEFAULT_PER_PAGE = 100
DEFAULT_RAW_ROOT = Path("raw/fec")


class FECError(RuntimeError):
    """Raised when the FEC API returns a non-recoverable error."""


def get_api_key() -> str:
    """Read ``FEC_API_KEY`` from env. Hard-fails with a clear message."""
    key = os.environ.get("FEC_API_KEY")
    if not key:
        raise FECError(
            "FEC_API_KEY env var not set. Get a free key at https://api.data.gov/signup/"
        )
    return key


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, httpx.TransportError | httpx.TimeoutException)


@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException)),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _request(client: httpx.Client, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Single FEC API call. Tenacity-retried on transient errors.

    Note: tenacity's ``retry_if_exception_type`` runs before our custom predicate,
    so 4xx other than 429 will be reraised (we re-raise inside the handler).
    """
    resp = client.get(f"{FEC_BASE_URL}/{endpoint.lstrip('/')}", params=params)
    if resp.status_code >= 400:
        # Distinguish retryable from non-retryable so tenacity does the right thing.
        if not _is_retryable(httpx.HTTPStatusError("", request=resp.request, response=resp)):
            raise FECError(f"FEC {endpoint} -> {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
    return resp.json()


def _archive(raw_root: Path, endpoint: str, page_index: int, payload: dict[str, Any]) -> Path:
    """Write a page response to disk. Returns the file path."""
    bucket = raw_root / endpoint.replace("/", "_")
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
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
    client: httpx.Client | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield each ``result`` row across all pages of ``endpoint``.

    Switches from offset to cursor pagination automatically when the result set
    is too deep for offset-based paging.
    """
    own_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        page_params = {"per_page": DEFAULT_PER_PAGE, **params, "api_key": api_key}
        page_params.setdefault("page", 1)

        page_index = 0
        cursor_mode = False
        last_indexes: dict[str, Any] | None = None

        while True:
            if cursor_mode and last_indexes:
                # Cursor mode: drop ``page`` and pass the last_indexes back as params.
                page_params.pop("page", None)
                for k, v in last_indexes.items():
                    # FEC names them ``last_index``, ``last_contribution_receipt_date``, etc.
                    page_params[k] = v

            payload = _request(client, endpoint, page_params)

            if archive:
                _archive(raw_root, endpoint, page_index, payload)

            results = payload.get("results", []) or []
            yield from results

            pagination = payload.get("pagination") or {}
            pages = pagination.get("pages", 1)
            current_page = pagination.get("page", page_params.get("page", 1))

            page_index += 1
            if max_pages is not None and page_index >= max_pages:
                return

            if not results:
                return

            # Decide next page.
            next_last = pagination.get("last_indexes")
            if cursor_mode:
                if not next_last:
                    return
                last_indexes = next_last
            elif current_page >= pages:
                # Offset mode exhausted. If FEC reports more results than offset
                # can reach (10k cap), flip to cursor mode.
                if next_last:
                    cursor_mode = True
                    last_indexes = next_last
                else:
                    return
            else:
                page_params["page"] = current_page + 1
    finally:
        if own_client:
            client.close()


def iter_committees(
    *,
    api_key: str,
    min_last_indexed: str | None = None,
    cycle: int | None = None,
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate raw committee dicts, optionally filtered by ``min_last_indexed``."""
    params: dict[str, Any] = {"sort": "committee_id"}
    if min_last_indexed:
        params["min_last_f1_date"] = min_last_indexed
    if cycle is not None:
        params["cycle"] = cycle
    yield from paginate(
        "committees", params, api_key=api_key, raw_root=raw_root,
        archive=archive, max_pages=max_pages,
    )


def iter_candidates(
    *,
    api_key: str,
    min_last_indexed: str | None = None,
    cycle: int | None = None,
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate raw candidate dicts."""
    params: dict[str, Any] = {"sort": "candidate_id"}
    if min_last_indexed:
        params["min_last_f2_date"] = min_last_indexed
    if cycle is not None:
        params["cycle"] = cycle
    yield from paginate(
        "candidates", params, api_key=api_key, raw_root=raw_root,
        archive=archive, max_pages=max_pages,
    )


def iter_contributions(
    *,
    api_key: str,
    min_date: str | None = None,
    committee_id: str | None = None,
    raw_root: Path = DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate raw Schedule A (itemized contribution) dicts.

    Schedule A is the deep endpoint -- always cursor-paginated past page 100.
    Scope tightly via ``committee_id`` and/or ``min_date`` to stay within the
    1,000 req/hr budget.
    """
    params: dict[str, Any] = {"sort": "contribution_receipt_date,sub_id"}
    if min_date:
        params["min_date"] = min_date
    if committee_id:
        params["committee_id"] = committee_id
    yield from paginate(
        "schedules/schedule_a", params, api_key=api_key, raw_root=raw_root,
        archive=archive, max_pages=max_pages,
    )
