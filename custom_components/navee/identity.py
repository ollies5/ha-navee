"""Owner identity import.

Ported from Volt (navee-offline) ``OwnerIdentityParser.java``. Accepts twelve hex characters,
a Volt setup file (``identity: …`` / ``role: …``), or a captured log containing checksum-verified
``Send authData: 55aa0030…`` owner-authentication frames.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import protocol

_MAX_INPUT = 1_048_576
_KEYS = r"(?:\"owner_identity\"|owner_identity|owner identity|\"identity\"|identity)"
_ID_FIELD = re.compile(
    rf"(?im)(?<![\w]){_KEYS}\s*:\s*\"?([0-9a-f]{{12}})(?=\"?(?:\s*(?:[,}}\r\n]|$)))"
)
_ID_KEY = re.compile(rf"(?i)(?<![\w]){_KEYS}\s*:")
_ROLE_FIELD = re.compile(
    r"(?im)(?<![\w])(?:\"role\"|role)\s*:\s*\"?([01])(?=\"?(?:\s*(?:[,}\r\n]|$)))"
)
_ROLE_KEY = re.compile(r"(?i)(?<![\w])(?:\"role\"|role)\s*:")
_SHARED_FIELD = re.compile(r"(?im)^\s*Shared-user option\s*:\s*(ON|OFF)\s*$")
_AUTH_MARKER = "Send authData:"
_FRAME_HEX = re.compile(r"(?i)[0-9a-f]{2}(?:[\s:-]*[0-9a-f]{2}){16}")


class IdentityError(ValueError):
    """The text holds no usable owner identity."""


@dataclass(frozen=True, slots=True)
class OwnerIdentity:
    identity: str
    """Twelve upper-case hex characters."""
    role: int


def parse(text: str) -> OwnerIdentity:
    if not text or not text.strip() or len(text) > _MAX_INPUT:
        raise IdentityError("empty")
    trimmed = text.strip()
    if re.fullmatch(r"(?i)[0-9a-f]{12}", trimmed):
        return OwnerIdentity(trimmed.upper(), protocol.ROLE_OWNER)

    explicit = None
    fields = 0
    for match in _ID_FIELD.finditer(text):
        fields += 1
        value = match.group(1).upper()
        if explicit is not None and explicit != value:
            raise IdentityError("ambiguous")
        explicit = value
    if len(_ID_KEY.findall(text)) != fields:
        raise IdentityError("format")
    if explicit is not None:
        return OwnerIdentity(explicit, _explicit_role(text))

    found: OwnerIdentity | None = None
    for line in text.splitlines():
        at = line.find(_AUTH_MARKER)
        if at < 0:
            continue
        candidate = line[at + len(_AUTH_MARKER) :].strip()
        if candidate.startswith("["):
            end = candidate.find("]")
            if end < 0:
                continue
            candidate = candidate[1:end].strip()
        # Never manufacture a packet by stripping arbitrary log text into hex.
        if not _FRAME_HEX.fullmatch(candidate):
            continue
        p = bytes.fromhex(re.sub(r"[\s:-]", "", candidate))
        if (
            len(p) != 17
            or p[0] != 0x55
            or p[1] != 0xAA
            or p[2] != 0
            or p[3] != protocol.AUTH
            or p[4] != 9
            or p[5] > 4
            or p[6] > 1
            or p[13] != 0
            or p[15] != 0xFE
            or p[16] != 0xFD
            or protocol.checksum(p[:14]) != p[14]
        ):
            continue
        captured = OwnerIdentity(p[7:13].hex().upper(), p[6])
        if found is not None and found != captured:
            raise IdentityError("ambiguous")
        found = captured
    if found is None:
        raise IdentityError("format")
    return found


def _explicit_role(text: str) -> int:
    role = -1
    fields = 0
    for match in _ROLE_FIELD.finditer(text):
        fields += 1
        value = int(match.group(1))
        if role not in (-1, value):
            raise IdentityError("ambiguous")
        role = value
    if len(_ROLE_KEY.findall(text)) != fields:
        raise IdentityError("format")
    for match in _SHARED_FIELD.finditer(text):
        value = 1 if match.group(1).upper() == "ON" else 0
        if role not in (-1, value):
            raise IdentityError("ambiguous")
        role = value
    return protocol.ROLE_OWNER if role == -1 else role
