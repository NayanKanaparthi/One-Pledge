"""OnePledge registry: the shared ledger of pledge locks of layer 2 (SPEC sections 5 to 7).

It receives tokens, never raw loan values, decides each submission in one database
transaction, and keeps an insert-only, hash-chained ledger as the only source of truth.
"""
