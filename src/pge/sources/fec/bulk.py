"""Bulk-download ingest from fec.gov (no API key required).

The pas2 file (PAC → candidate contributions) is exactly what we need
for the "PACs that support a politician" view. Combined with the
committee master file (cm), we can build:

  Curated Company  --[Affiliation]-->  PAC  --[Donation]-->  Politician

without ever calling the rate-limited FEC REST API.

Flow
----
1. Download cm{cycle}.zip and pas{cycle}.zip from
   https://www.fec.gov/files/bulk-downloads/{cycle_full}/.
2. Parse cm.txt -> map FEC ``connected_organization_name`` to a curated
   :class:`CuratedCompany`. Build PAC nodes for every committee whose
   sponsor is one of our curated companies, plus Company nodes for the
   sponsors. Add an ``OwnershipStake``-style affiliation -- using the
   schema's existing ``BusinessPartnership`` edge with relation="parent".
3. Parse pas2.txt -> for each contribution from a tracked PAC to a
   current member of Congress (via the legislators index), write a
   Donation edge.

Caveats
-------
* The download is ~50 MB compressed, ~500 MB uncompressed. We stream-parse
  to avoid loading the whole thing into memory.
* The pas2 file uses pipe (``|``) as a separator and has no header row.
  Column order is fixed by FEC; documented inline in :data:`PAS2_COLS`.
* Cycle is 2-digit in the filenames (``pas24.zip``, not ``pas2024.zip``)
  but 4-digit in the URL path. Annoying but consistent.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx

from pge.graph.db import GraphDB
from pge.graph.ingest import upsert_edge, upsert_node
from pge.schema.edges import BusinessPartnershipEdge, DonationEdge
from pge.schema.nodes import CompanyNode, PACNode
from pge.seed.companies import CURATED_COMPANIES, CuratedCompany, find_for_organization
from pge.sources.congress.resolve import LegislatorIndex

# Column order for cm.txt (FEC committee master). One row per committee.
# https://www.fec.gov/campaign-finance-data/committee-master-file-description/
CM_COLS = [
    "CMTE_ID", "CMTE_NM", "TRES_NM", "CMTE_ST1", "CMTE_ST2", "CMTE_CITY",
    "CMTE_ST", "CMTE_ZIP", "CMTE_DSGN", "CMTE_TP", "CMTE_PTY_AFFILIATION",
    "CMTE_FILING_FREQ", "ORG_TP", "CONNECTED_ORG_NM", "CAND_ID",
]

# Column order for itpas2.txt (contributions from PACs to candidates).
# https://www.fec.gov/campaign-finance-data/contributions-to-candidates-from-committees-file-description/
PAS2_COLS = [
    "CMTE_ID", "AMNDT_IND", "RPT_TP", "TRANSACTION_PGI", "IMAGE_NUM",
    "TRANSACTION_TP", "ENTITY_TP", "NAME", "CITY", "STATE", "ZIP_CODE",
    "EMPLOYER", "OCCUPATION", "TRANSACTION_DT", "TRANSACTION_AMT",
    "OTHER_ID", "CAND_ID", "TRAN_ID", "FILE_NUM", "MEMO_CD", "MEMO_TEXT",
    "SUB_ID",
]


def _bulk_url(cycle: int, prefix: str) -> str:
    """Build the bulk-download URL.

    ``prefix`` is e.g. ``cm`` (committee master) or ``pas2`` (PAC->candidate).
    """
    suffix = str(cycle)[-2:]  # 2024 -> "24"
    return f"https://www.fec.gov/files/bulk-downloads/{cycle}/{prefix}{suffix}.zip"


def _download(url: str, cache_dir: Path) -> Path:
    """Download a file to ``cache_dir`` if missing. Returns the local path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / Path(url).name
    if target.exists() and target.stat().st_size > 0:
        return target
    with (
        httpx.Client(timeout=300.0, follow_redirects=True) as client,
        client.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        with target.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
    return target


def _iter_zip_rows(
    zip_path: Path, member_name: str, columns: list[str]
) -> Iterator[dict[str, str]]:
    """Stream-parse a single text file inside a zip as pipe-delimited."""
    with zipfile.ZipFile(zip_path) as zf:
        try:
            info = zf.getinfo(member_name)
        except KeyError:
            # FEC sometimes ships ``itpas2.txt`` directly, sometimes
            # nested inside a folder. Try the basename match.
            candidates = [n for n in zf.namelist() if n.endswith(member_name)]
            if not candidates:
                raise
            info = zf.getinfo(candidates[0])
        with zf.open(info) as raw:
            text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
            reader = csv.reader(text, delimiter="|", quoting=csv.QUOTE_NONE)
            for row in reader:
                if not row:
                    continue
                # Pad short rows so dict construction never KeyErrors.
                if len(row) < len(columns):
                    row = row + [""] * (len(columns) - len(row))
                yield dict(zip(columns, row, strict=False))


def build_pac_company_map(
    cm_zip: Path,
) -> tuple[dict[str, CuratedCompany], dict[str, str]]:
    """Walk the committee master, return:

    * ``pac_to_company``: FEC committee_id -> matched CuratedCompany.
    * ``pac_names``: FEC committee_id -> committee name (for node naming).
    """
    pac_to_company: dict[str, CuratedCompany] = {}
    pac_names: dict[str, str] = {}
    for row in _iter_zip_rows(cm_zip, "cm.txt", CM_COLS):
        cmte_id = row.get("CMTE_ID", "").strip()
        if not cmte_id:
            continue
        connected = row.get("CONNECTED_ORG_NM", "")
        company = find_for_organization(connected)
        if company is None:
            continue
        pac_to_company[cmte_id] = company
        pac_names[cmte_id] = row.get("CMTE_NM", "").strip() or cmte_id
    return pac_to_company, pac_names


def write_company_and_pacs(
    db: GraphDB,
    pac_to_company: dict[str, CuratedCompany],
    pac_names: dict[str, str],
) -> dict[str, int]:
    """Write Company nodes for every curated company that had a matching
    PAC, plus PAC nodes for those PACs, plus a parent-of edge linking them."""
    seen_companies: set[str] = set()
    company_count = 0
    pac_count = 0
    affil_count = 0

    for cmte_id, company in pac_to_company.items():
        company_id = f"co:seed:{company.slug}"
        if company.slug not in seen_companies:
            upsert_node(
                db,
                CompanyNode(
                    id=company_id,
                    name=company.name,
                    external_ids={"seed": company.slug},
                    domain=company.domain,
                    hq_city=company.hq_city,
                    hq_state=company.hq_state,
                    latitude=company.latitude,
                    longitude=company.longitude,
                ),
            )
            seen_companies.add(company.slug)
            company_count += 1

        pac_id = f"pac:{cmte_id}"
        upsert_node(
            db,
            PACNode(
                id=pac_id,
                name=pac_names.get(cmte_id, cmte_id),
                external_ids={"fec": cmte_id},
                fec_committee_id=cmte_id,
                pac_type="corporate",
                affiliated_org=company.name,
            ),
        )
        pac_count += 1

        # Company -> its PAC. We use BusinessPartnership(relation='parent')
        # because the schema's OwnershipStake wants a percentage; the
        # PAC->parent relationship doesn't naturally carry one.
        upsert_edge(
            db,
            BusinessPartnershipEdge(
                id=f"seed:affil:{company.slug}:{cmte_id}",
                src_id=company_id,
                dst_id=pac_id,
                evidence_type="VERIFIED",
                source_name="fec_cm",
                source_id=cmte_id,
                relation="parent",
                confidence="high",
                strength="strong",
            ),
        )
        affil_count += 1

    return {
        "companies": company_count,
        "pacs": pac_count,
        "affiliations": affil_count,
    }


def write_donations(
    db: GraphDB,
    pas2_zip: Path,
    pac_ids: set[str],
    fec_to_bioguide: dict[str, str],
) -> int:
    """Stream pas2.txt; for each row whose sender is a tracked PAC and whose
    recipient candidate maps to a current bioguide, write a Donation edge."""
    written = 0
    for row in _iter_zip_rows(pas2_zip, "itpas2.txt", PAS2_COLS):
        cmte_id = row.get("CMTE_ID", "").strip()
        if cmte_id not in pac_ids:
            continue
        cand_id = row.get("CAND_ID", "").strip() or row.get("OTHER_ID", "").strip()
        if not cand_id:
            continue
        bioguide = fec_to_bioguide.get(cand_id)
        if not bioguide:
            continue

        amount_str = row.get("TRANSACTION_AMT", "0").strip() or "0"
        try:
            amount_dollars = float(amount_str)
        except ValueError:
            continue
        amount_cents = max(int(round(amount_dollars * 100)), 0)
        if amount_cents == 0:
            continue  # ignore in-kind / informational rows

        date_str = row.get("TRANSACTION_DT", "").strip()
        # FEC dates: MMDDYYYY (no separators). Tolerant parse.
        as_of_iso: str | None = None
        if len(date_str) == 8 and date_str.isdigit():
            mm, dd, yyyy = date_str[:2], date_str[2:4], date_str[4:]
            as_of_iso = f"{yyyy}-{mm}-{dd}"

        sub_id = (row.get("SUB_ID") or row.get("TRAN_ID") or "").strip()
        if not sub_id:
            continue

        try:
            edge = DonationEdge(
                id=f"fec:contrib:{sub_id}",
                src_id=f"pac:{cmte_id}",
                dst_id=f"pol:{bioguide}",
                evidence_type="VERIFIED",
                source_name="fec_pas2",
                source_id=sub_id,
                amount_cents=amount_cents,
                # Pydantic accepts ISO date string and parses it.
                as_of_date=as_of_iso,  # type: ignore[arg-type]
                strength="strong",
                confidence="high",
            )
        except Exception:
            # Defensive: skip malformed rows rather than abort the whole run.
            continue
        upsert_edge(db, edge)
        written += 1
    return written


def ingest_bulk_pas2(
    db: GraphDB,
    *,
    cycle: int,
    legislators_index: LegislatorIndex,
    cache_dir: Path = Path("data/fec_bulk"),
) -> dict[str, int]:
    """End-to-end orchestrator. Idempotent."""
    cm_url = _bulk_url(cycle, "cm")
    pas2_url = _bulk_url(cycle, "pas")
    cm_zip = _download(cm_url, cache_dir)
    pas2_zip = _download(pas2_url, cache_dir)

    pac_to_company, pac_names = build_pac_company_map(cm_zip)
    structural = write_company_and_pacs(db, pac_to_company, pac_names)

    pac_ids = set(pac_to_company)
    fec_to_bioguide = dict(legislators_index.fec_to_bioguide)
    donations = write_donations(db, pas2_zip, pac_ids, fec_to_bioguide)

    return {
        **structural,
        "donations": donations,
        "tracked_pacs": len(pac_ids),
        "tracked_companies": len({c.slug for c in pac_to_company.values()}),
    }


# Helper exposed for tests with a tiny in-process zip rather than the
# real fec.gov download.
def ingest_from_local_zips(
    db: GraphDB,
    *,
    cm_zip: Path,
    pas2_zip: Path,
    legislators_index: LegislatorIndex,
) -> dict[str, int]:
    pac_to_company, pac_names = build_pac_company_map(cm_zip)
    structural = write_company_and_pacs(db, pac_to_company, pac_names)
    pac_ids = set(pac_to_company)
    fec_to_bioguide = dict(legislators_index.fec_to_bioguide)
    donations = write_donations(db, pas2_zip, pac_ids, fec_to_bioguide)
    return {**structural, "donations": donations}


def curated_company_count() -> int:
    """For sanity logging during build."""
    return len(CURATED_COMPANIES)


# Re-export for type-friendly external use.
__all__ = [
    "ingest_bulk_pas2",
    "ingest_from_local_zips",
    "build_pac_company_map",
    "write_company_and_pacs",
    "write_donations",
    "curated_company_count",
]
