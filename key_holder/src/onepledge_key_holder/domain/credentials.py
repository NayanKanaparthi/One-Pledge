"""How an API key is stored and looked up: by its SHA-256 only (`key_holder.yaml` `MemberApiKey`)."""

from __future__ import annotations

import hashlib


def api_key_sha256(api_key: str) -> str:
    """The lower case SHA-256 hex of an API key, the only form the holder keeps."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
