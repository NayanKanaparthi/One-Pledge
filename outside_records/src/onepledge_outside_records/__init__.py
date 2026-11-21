"""Outside records: a read-only stand-in for title offices, payment deposits and a contract vault (SPEC 6).

The only service a bank agent sends a raw VIN to. It answers three lookups for
authenticated bank clients, never writes through its runtime role, and never logs a
path, a query string or a VIN.
"""
