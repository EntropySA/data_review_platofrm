"""Small authentication interface shared by the Streamlit UI and tests."""

import hmac
from typing import Optional


class ConfigurationError(RuntimeError):
    pass


def authenticate(
    entered_password: str, reviewer_password: str, admin_password: str
) -> Optional[str]:
    if not reviewer_password or not admin_password:
        raise ConfigurationError("Both reviewer and admin passwords must be configured.")
    if hmac.compare_digest(reviewer_password, admin_password):
        raise ConfigurationError("Reviewer and admin passwords must be different.")
    if hmac.compare_digest(entered_password, admin_password):
        return "admin"
    if hmac.compare_digest(entered_password, reviewer_password):
        return "reviewer"
    return None
