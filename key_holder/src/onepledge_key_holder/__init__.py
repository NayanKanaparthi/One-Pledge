"""OnePledge key holder: one of the two OPRF holders of layer 2 (SPEC section 4).

Two processes run this package, `kh1` and `kh2`, each with its own secret scalar and its own
database. A holder evaluates blinded elements and never sees a raw loan value.
"""
