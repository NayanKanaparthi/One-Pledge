"""TEST DOUBLES for the layer 2 key holder and registry, and the contract checker.

Everything in this package is a contract test double: an in-memory stand-in that
behaves as `contracts/key_holder.yaml` and `contracts/registry.yaml` describe, so
layer 1 can be verified before layer 2 exists. It is not production code, it keeps
no state across restarts, and nothing in layers 2 or 3 imports it outside tests.
"""
