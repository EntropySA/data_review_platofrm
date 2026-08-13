from datetime import datetime, timezone

import pytest

from cloudflare_app.worker.src.auth import (
    AuthenticationError,
    authenticate_password,
    issue_token,
    verify_token,
)


def test_password_role_and_signed_session_round_trip():
    identity = authenticate_password(
        entered="review-secret",
        reviewer_password="review-secret",
        admin_password="admin-secret",
        reviewer_name="أمينة",
    )
    assert identity.role == "reviewer"
    assert identity.name == "أمينة"

    token = issue_token(
        identity, "signing-secret", now=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    restored = verify_token(
        token, "signing-secret", now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
    )
    assert restored == identity


def test_invalid_or_expired_sessions_are_rejected():
    identity = authenticate_password("admin", "review", "admin", "")
    token = issue_token(
        identity, "signing-secret", now=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    with pytest.raises(AuthenticationError):
        verify_token(token + "x", "signing-secret")
    with pytest.raises(AuthenticationError, match="expired"):
        verify_token(
            token, "signing-secret", now=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
