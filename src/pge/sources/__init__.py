"""Source ingestors.

Each source lives in its own subpackage with three modules:
- ``fetch``: pulls raw data from API or bulk download into ``raw/<source>/``
- ``parse``: raw bytes -> typed Pydantic models for that source
- ``to_graph``: source models -> ``upsert_node`` / ``upsert_edge`` calls

Sources should never write to the DB except through ``pge.graph.ingest``.
"""
