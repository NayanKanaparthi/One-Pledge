"""The contract checker `onepledge-contracts check` (SPEC 7.4).

It validates the four OpenAPI files, every example against its schema, the
example references, and recomputes the published test vectors. It ships with the
test doubles because both exist to keep every layer honest about the contracts.
"""
