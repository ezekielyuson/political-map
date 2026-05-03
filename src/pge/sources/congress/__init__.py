"""Congress.gov ingest (api.congress.gov v3).

Sub-modules:
- :mod:`fetch`     -- HTTP, retry, pagination
- :mod:`resolve`   -- bioguide<->FEC mapping from `unitedstates/congress-legislators`
- :mod:`parse`     -- Pydantic models for member / committee responses
- :mod:`to_graph`  -- mappers + entity resolution at write time
- :mod:`ingest`    -- orchestrator (called by the CLI)
"""
