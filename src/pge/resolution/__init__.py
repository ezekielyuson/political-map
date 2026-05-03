"""Entity resolution.

For now this is just :mod:`individuals`, which clusters ``Individual`` nodes
across FEC-derived donor records. The same building blocks (``extract``,
``block``, ``score``, ``apply``) generalize to other node kinds when we add
SEC EDGAR / FARA / etc. and need cross-source dedup of executives or filers.
"""
