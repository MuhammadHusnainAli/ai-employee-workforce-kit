"""UUIDv7: time-ordered ids, so outbox and event ids sort by creation time."""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a UUIDv7 (RFC 9562).

    Layout: 48-bit Unix ms timestamp | 4-bit version (7) | 12 random bits |
    2-bit variant (0b10) | 62 random bits.
    """
    ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFF_FFFF_FFFF_FFFF
    value = (ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)
