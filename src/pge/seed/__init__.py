"""Curated reference data baked into the deploy.

Two datasets:

* :mod:`locations` -- state capital coordinates for politician geocoding.
* :mod:`companies` -- a hand-picked list of major corporate PAC sponsors
  with their HQ coordinates, domain (for logo lookup), and search aliases
  for matching FEC committee_master ``connected_organization`` strings.

Both are static; updating them is a code change, not a data ingest.
"""
