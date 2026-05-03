"""FEC ingest (Federal Election Commission, api.open.fec.gov).

Sub-modules:
- :mod:`fetch`     -- HTTP, retry, pagination, raw-on-disk archival
- :mod:`parse`     -- Pydantic models for FEC API responses
- :mod:`to_graph`  -- raw models -> graph upserts
- :mod:`ingest`    -- orchestrator (called by the CLI)
"""
