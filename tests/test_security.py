import pytest

from security import ConfigurationError, authenticate


def test_password_selects_role_without_exposing_secret_values():
    assert authenticate("review-secret", "review-secret", "admin-secret") == "reviewer"
    assert authenticate("admin-secret", "review-secret", "admin-secret") == "admin"
    assert authenticate("wrong", "review-secret", "admin-secret") is None

    with pytest.raises(ConfigurationError):
        authenticate("same", "same", "same")
