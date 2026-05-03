"""Senate LDA (Lobbying Disclosure Act) ingest.

Sub-modules:
- :mod:`fetch`     -- HTTP client for ``lda.senate.gov/api/v1``
- :mod:`parse`     -- Pydantic models for filing payloads
- :mod:`to_graph`  -- filing -> LobbyingFirm / Company / LobbyingContract
- :mod:`ingest`    -- orchestrator (called by the CLI)
"""
