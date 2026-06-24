"""TOTP generation + best-effort NTP clock-skew detection.

Per [D-116] D15 + D17 cascade 2026-06-25 -- HILDA generates ephemeral 6-digit
TOTP code per upload via pyotp.TOTP(seed).now(); long-lived seed lives in
credential_service sops vault and NEVER leaves the call frame.

CAD-W005 surfaces clock drift when host clock is off NTP by more than the
configured threshold (default 25s -- TOTP window is ~30s so 25s leaves margin).

NFR-2: this module NEVER logs the seed or the generated code.
"""
from __future__ import annotations

import socket
import struct
import time

import pyotp

__all__ = ["current_totp", "ntp_skew_seconds"]


# SNTP epoch offset (seconds between 1900-01-01 and 1970-01-01)
_NTP_EPOCH_OFFSET = 2_208_988_800


def current_totp(seed: str) -> str:
    """Returns the current 6-digit TOTP code derived from a base32 seed via pyotp.

    Pure-function wrapper. Caller MUST NOT log the return value.
    """
    return pyotp.TOTP(seed).now()


def ntp_skew_seconds(
    ntp_host: str = "pool.ntp.org",
    timeout: float = 2.0,
) -> float | None:
    """Best-effort SNTP probe; returns abs(local_time - ntp_time) in seconds.

    Returns None on any network/socket failure -- caller treats this as
    "skew unknown; do not warn". Used by --diagnostic mode + best-effort
    pre-upload check; never raises.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        # SNTP v3 client request -- 48 bytes; first byte LI=0 / VN=3 / Mode=3
        # (0x1b == 0b00_011_011).
        packet = b"\x1b" + 47 * b"\0"
        sock.sendto(packet, (ntp_host, 123))
        local_send = time.time()
        data, _ = sock.recvfrom(1024)
        local_recv = time.time()
        if len(data) < 48:
            return None
        # Transmit timestamp lives at bytes 40..48 (seconds + fraction, uint32 each).
        secs, frac = struct.unpack("!II", data[40:48])
        ntp_time = secs + frac / 2**32 - _NTP_EPOCH_OFFSET
        # Compare to the midpoint of our send/recv to cancel out half the
        # round-trip (best-effort; not Marzullo).
        local_mid = (local_send + local_recv) / 2
        return abs(local_mid - ntp_time)
    except (OSError, socket.timeout, struct.error):
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
