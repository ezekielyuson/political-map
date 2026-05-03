"""Map LDA filings into graph nodes/edges.

ID conventions
--------------
* Lobbying firm (registrant)  ``lf:lda:<registrant_id>``
* Client (company)            ``co:lda:<client_id>``
* Lobbying contract (filing)  ``lda:filing:<filing_uuid>``

Why one edge per filing?
------------------------
LDA filings are quarterly. A single firm/client relationship typically
produces 4 filings per year. Each filing has its own income, its own set of
issue codes, and its own as-of date, so each becomes a separate
``LobbyingContract`` edge. Aggregation across filings is a query-time
concern.

Issue codes
-----------
A single LD-2 filing contains 1+ ``lobbying_activities``, each with one
``general_issue_code``. We collect them onto the edge as a deduped list. The
edge's ``notes`` field optionally captures the per-activity descriptions
joined together (kept short to avoid bloating storage).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pge.graph.db import GraphDB
from pge.graph.ingest import upsert_edge, upsert_node
from pge.schema.edges import LobbyingContractEdge
from pge.schema.nodes import CompanyNode, LobbyingFirmNode
from pge.sources.lda.fetch import PERIOD_TO_QUARTER
from pge.sources.lda.parse import LDAClient, LDAFiling, LDARegistrant

SOURCE_NAME = "lda"


def _money_to_cents(value: str | None) -> int | None:
    """LDA returns income/expenses as decimal strings; round to int cents."""
    if value is None or value == "":
        return None
    try:
        cents = int((Decimal(value) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None
    return max(cents, 0)


def _quarter_label(filing_year: int, filing_period: str | None) -> str | None:
    """Combine ``filing_year + filing_period`` into our compact quarter label."""
    if not filing_period:
        return None
    suffix = PERIOD_TO_QUARTER.get(filing_period.lower())
    if not suffix:
        return None
    return f"{filing_year}{suffix}"


def registrant_to_node(reg: LDARegistrant) -> LobbyingFirmNode:
    return LobbyingFirmNode(
        id=f"lf:lda:{reg.id}",
        name=reg.name,
        external_ids={SOURCE_NAME: str(reg.id)},
        lda_registrant_id=str(reg.id),
    )


def client_to_node(cli: LDAClient) -> CompanyNode:
    return CompanyNode(
        id=f"co:lda:{cli.id}",
        name=cli.name,
        external_ids={SOURCE_NAME: str(cli.id)},
    )


def _collect_issue_codes(filing: LDAFiling) -> list[str]:
    """Deduped issue codes preserving first-seen order."""
    seen: dict[str, None] = {}
    for activity in filing.lobbying_activities:
        if activity.general_issue_code:
            seen.setdefault(activity.general_issue_code, None)
    return list(seen)


def filing_to_edge(filing: LDAFiling) -> LobbyingContractEdge:
    """Build the LobbyingContract edge for one filing.

    The recipient (client) and provider (lobbying firm) nodes must already
    exist; :func:`write_filing` handles that.
    """
    income_cents = _money_to_cents(filing.income)
    expenses_cents = _money_to_cents(filing.expenses)
    # LD-2 reporters use one of "income" or "expenses" depending on the
    # method; we prefer income and fall back to expenses so we record the
    # economic flow regardless of which side filed.
    amount_cents = income_cents if income_cents is not None else expenses_cents

    posted_date = filing.dt_posted.date() if filing.dt_posted else None

    return LobbyingContractEdge(
        id=f"lda:filing:{filing.filing_uuid}",
        src_id=f"co:lda:{filing.client.id}",
        dst_id=f"lf:lda:{filing.registrant.id}",
        evidence_type="VERIFIED",
        source_name=SOURCE_NAME,
        source_id=filing.filing_uuid,
        amount_cents=amount_cents,
        quarter=_quarter_label(filing.filing_year, filing.filing_period),
        issue_codes=_collect_issue_codes(filing),
        as_of_date=posted_date,
        strength="strong",
        confidence="high",
    )


def write_filing(db: GraphDB, filing: LDAFiling) -> None:
    """Upsert the registrant node, the client node, and the contract edge."""
    upsert_node(db, registrant_to_node(filing.registrant))
    upsert_node(db, client_to_node(filing.client))
    upsert_edge(db, filing_to_edge(filing))
