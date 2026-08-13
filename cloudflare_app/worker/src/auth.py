"""Password authentication and signed stateless sessions for the Worker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class Identity:
    role: str
    name: str
    session_id: str


def authenticate_password(
    entered: str,
    reviewer_password: str,
    admin_password: str,
    reviewer_name: str,
) -> Identity:
    if not reviewer_password or not admin_password or reviewer_password == admin_password:
        raise AuthenticationError("Authentication is not configured safely.")
    if hmac.compare_digest(entered, admin_password):
        return Identity("admin", "Admin", str(uuid.uuid4()))
    if hmac.compare_digest(entered, reviewer_password):
        name = reviewer_name.strip()
        if not name:
            raise AuthenticationError("Reviewer name is required.")
        return Identity("reviewer", name, str(uuid.uuid4()))
    raise AuthenticationError("Incorrect password.")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(
    identity: Identity,
    secret: str,
    now: datetime | None = None,
    lifetime: timedelta = timedelta(hours=12),
) -> str:
    current = now or datetime.now(timezone.utc)
    payload = {**asdict(identity), "exp": int((current + lifetime).timestamp())}
    encoded = _encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    signature = _encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_token(token: str, secret: str, now: datetime | None = None) -> Identity:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected = _encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied_signature, expected):
            raise AuthenticationError("Invalid session.")
        payload = json.loads(_decode(encoded))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthenticationError("Invalid session.") from exc
    timestamp = int((now or datetime.now(timezone.utc)).timestamp())
    if int(payload["exp"]) <= timestamp:
        raise AuthenticationError("Session expired.")
    if payload.get("role") not in {"reviewer", "admin"}:
        raise AuthenticationError("Invalid session.")
    return Identity(payload["role"], payload["name"], payload["session_id"])
