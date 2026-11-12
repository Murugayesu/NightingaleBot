"""Authentication and rate limiting.

* Admin endpoints: a signed-in admin account (see api/auth.py) or, for
  automation, a shared secret in the X-Admin-API-Key header.
* Guest chat: an anonymous session token issued by POST /chat/session.
  The token is "<session_id>.<expiry>.<HMAC signature>": the server can verify
  it without a lookup, and nobody can forge one without SESSION_SECRET.
* Rate limits: per client IP. X-Forwarded-For is only believed when the
  request comes from a trusted proxy network, so clients cannot spoof it.
"""

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from functools import lru_cache

from fastapi import Request
from slowapi import Limiter

from nursing_bot.config import get_settings


# ------------------------------------------------------------------ client IP
@lru_cache
def _trusted_networks(
    spec: str,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(
        ipaddress.ip_network(n.strip(), strict=False)
        for n in spec.split(",")
        if n.strip()
    )


def _is_trusted(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(
        address in net
        for net in _trusted_networks(get_settings().TRUSTED_PROXY_NETWORKS)
    )


def get_client_ip(request: Request) -> str:
    """The real client address, honouring X-Forwarded-For only from trusted proxies.

    Walks the X-Forwarded-For chain from the right (nearest hop) and returns
    the first address that is not one of our own proxies.
    """
    peer = request.client.host if request.client else "unknown"
    if not _is_trusted(peer):
        return peer
    forwarded = [
        h.strip()
        for h in request.headers.get("x-forwarded-for", "").split(",")
        if h.strip()
    ]
    for hop in reversed(forwarded):
        if not _is_trusted(hop):
            return hop
    return forwarded[0] if forwarded else peer


limiter = Limiter(key_func=get_client_ip)


# ------------------------------------------------------------------ admin key
def admin_key_valid(presented: str | None) -> bool:
    """Constant-time check of a presented admin key against the configured one."""
    configured = get_settings().ADMIN_API_KEY.get_secret_value()
    if not configured or not presented:
        return False
    return secrets.compare_digest(presented.encode(), configured.encode())


# -------------------------------------------------------------- chat sessions
def _signature(payload: str) -> str:
    key = get_settings().SESSION_SECRET.get_secret_value().encode()
    digest = hmac.new(key, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def issue_session_token(session_id: str, expires_at: int) -> str:
    payload = f"{session_id}.{expires_at}"
    return f"{payload}.{_signature(payload)}"


def verify_session_token(token: str, now: float | None = None) -> str | None:
    """Return the session id if the token is authentic and unexpired, else None."""
    try:
        session_id, expires_raw, signature = token.rsplit(".", 2)
        expires_at = int(expires_raw)
    except ValueError:
        return None
    expected = _signature(f"{session_id}.{expires_at}")
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        return None
    if expires_at < (now if now is not None else time.time()):
        return None
    return session_id
