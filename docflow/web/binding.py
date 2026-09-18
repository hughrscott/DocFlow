"""Local server bind policy: DocFlow listens on loopback only (no remote UI)."""
from __future__ import annotations

import ipaddress

DEFAULT_HOST = "127.0.0.1"


class NonLoopbackBindError(ValueError):
    """A bind address would expose the local UI beyond this machine."""


def require_loopback_host(host: str) -> str:
    """Return ``host`` if it is ``localhost`` or a loopback IP literal; never resolves DNS."""
    if host == "localhost":
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise NonLoopbackBindError("host must be a loopback address") from None
    if not address.is_loopback:
        raise NonLoopbackBindError("host must be a loopback address")
    return host
