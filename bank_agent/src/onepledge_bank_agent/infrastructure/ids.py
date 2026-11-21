"""The identifier generator (the `IdGenerator` port): random UUID version 4."""

from __future__ import annotations

import uuid


class RandomIds:
    """New UUID version 4 values from the operating system's random source."""

    def new_id(self) -> uuid.UUID:
        """A fresh UUID version 4."""
        return uuid.uuid4()
